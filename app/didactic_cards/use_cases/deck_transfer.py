from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from ..domain.entities import (
    Card,
    CardDeck,
    Deck,
    CardModeError,
    validate_card_deck_mode,
    validate_card_mode_fields,
)
from ..domain.interfaces import DeckRepository
from ..domain.rendering import AuthoringMode, DeckRenderSettings
from ..domain.trusted import (
    TemplateProvenance,
    TrustedTemplateVersion,
)


DECK_EXPORT_SCHEMA_VERSION = 8
SUPPORTED_DECK_EXPORT_SCHEMAS = {DECK_EXPORT_SCHEMA_VERSION}


class DeckTransferError(ValueError):
    pass


def _validated_cards_for_transfer(
    repo: DeckRepository,
    deck: Deck,
) -> CardDeck:
    try:
        cards = repo.load_cards(deck.id)
        validate_card_deck_mode(
            cards, deck.render_settings.authoring_mode
        )
    except CardModeError as error:
        raise DeckTransferError(
            'Колода нарушает контракт Safe/Advanced и не может быть '
            'экспортирована.'
        ) from error
    return cards


def export_deck_json(repo: DeckRepository, deck_id: str) -> bytes:
    deck = repo.get_deck(deck_id)
    if deck is None:
        raise KeyError(deck_id)
    cards = _validated_cards_for_transfer(repo, deck)
    payload = {
        'schema_version': DECK_EXPORT_SCHEMA_VERSION,
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'deck': deck.to_dict(),
        'cards': cards.to_list(),
    }
    list_templates = getattr(repo, 'list_trusted_templates', None)
    payload['trusted_templates'] = (
        [
            {
                'id': template.id,
                'version': template.version,
                'front_source': template.front_source,
                'back_source': template.back_source,
                'source_hash': template.source_hash,
                'source_provenance': template.provenance.value,
            }
            for template in list_templates(deck_id)
        ]
        if (
            list_templates is not None
            and deck.render_settings.authoring_mode is AuthoringMode.ADVANCED
        )
        else []
    )
    return json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')


def export_deck_csv(repo: DeckRepository, deck_id: str) -> bytes:
    deck = repo.get_deck(deck_id)
    if deck is None:
        raise KeyError(deck_id)
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';', lineterminator='\n')
    advanced = (
        deck.render_settings.authoring_mode is AuthoringMode.ADVANCED
    )
    writer.writerow(
        ['section', 'front', 'back', 'upper_header', 'lower_header']
        if advanced
        else ['section', 'front', 'back']
    )
    for card in _validated_cards_for_transfer(repo, deck).cards:
        row = [card.section, card.front, card.back]
        if advanced:
            row.extend([card.upper_header, card.lower_header])
        writer.writerow(row)
    return ('\ufeff' + output.getvalue()).encode('utf-8')


def export_card_csv_template(authoring_mode: AuthoringMode | str) -> bytes:
    mode = AuthoringMode(authoring_mode)
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';', lineterminator='\n')
    writer.writerow(
        ['section', 'front', 'back', 'upper_header', 'lower_header']
        if mode is AuthoringMode.ADVANCED
        else ['section', 'front', 'back']
    )
    return ('\ufeff' + output.getvalue()).encode('utf-8')


def import_deck_json(
    repo: DeckRepository,
    source: bytes,
    max_cards: int | None = None,
) -> Deck:
    try:
        payload = json.loads(source.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DeckTransferError(f'Некорректный JSON: {error}') from error
    if not isinstance(payload, dict):
        raise DeckTransferError('Корень export-файла должен быть объектом')
    schema_version = payload.get('schema_version')
    if schema_version not in SUPPORTED_DECK_EXPORT_SCHEMAS:
        raise DeckTransferError('Неподдерживаемая версия export-схемы')
    deck_data = payload.get('deck')
    card_items = payload.get('cards')
    if not isinstance(deck_data, dict) or not isinstance(card_items, list):
        raise DeckTransferError('Export должен содержать deck и cards')
    name = deck_data.get('name')
    description = deck_data.get('description', '')
    source_deck_id = deck_data.get('id')
    if not isinstance(name, str) or not name.strip():
        raise DeckTransferError('Название колоды отсутствует')
    if not isinstance(description, str):
        raise DeckTransferError('Описание колоды должно быть строкой')
    if source_deck_id is not None and not isinstance(source_deck_id, str):
        raise DeckTransferError('ID исходной колоды должен быть строкой')
    settings_data = deck_data.get('render_settings')
    if settings_data is None:
        raise DeckTransferError('Export не содержит настройки оформления')
    try:
        render_settings = DeckRenderSettings.from_dict(settings_data)
    except (TypeError, ValueError) as error:
        raise DeckTransferError(
            f'Некорректные настройки оформления: {error}'
        ) from error
    if max_cards is not None and len(card_items) > max_cards:
        raise DeckTransferError(f'Максимум карточек в колоде: {max_cards}')

    cards: list[Card] = []
    source_ids: set[str] = set()
    for index, item in enumerate(card_items, start=1):
        if not isinstance(item, dict):
            raise DeckTransferError(f'Карточка {index} должна быть объектом')
        front = item.get('front', '')
        back = item.get('back', '')
        section = item.get('section', '')
        upper_header = item.get('upper_header', '')
        lower_header = item.get('lower_header', '')
        source_card_id = item.get('id')
        if not isinstance(front, str) or not isinstance(back, str):
            raise DeckTransferError(f'Стороны карточки {index} должны быть строками')
        if not isinstance(section, str):
            raise DeckTransferError(f'Секция карточки {index} должна быть строкой')
        if not isinstance(upper_header, str) or not isinstance(lower_header, str):
            raise DeckTransferError(
                f'Колонтитулы карточки {index} должны быть строками'
            )
        if source_card_id is not None and not isinstance(source_card_id, str):
            raise DeckTransferError(f'ID карточки {index} должен быть строкой')
        if source_card_id and source_card_id in source_ids:
            raise DeckTransferError(f'Повтор ID карточки {index}')
        if source_card_id:
            source_ids.add(source_card_id)
        card = Card(
            front=front,
            back=back,
            section=section,
            upper_header=upper_header,
            lower_header=lower_header,
            parent_id=source_card_id,
        )
        try:
            validate_card_mode_fields(card, render_settings.authoring_mode)
        except CardModeError as error:
            raise DeckTransferError(f'Карточка {index}: {error}') from error
        cards.append(card)

    template_items = payload.get('trusted_templates')
    if not isinstance(template_items, list):
        raise DeckTransferError('Export должен содержать trusted_templates')
    trusted_templates: list[TrustedTemplateVersion] = []
    template_ids: set[str] = set()
    template_versions: set[int] = set()
    expected_fields = {
        'id', 'version', 'source_hash', 'source_provenance',
        'front_source', 'back_source',
    }
    for index, item in enumerate(template_items, start=1):
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise DeckTransferError(
                f'Trusted-шаблон {index} имеет неверные поля'
            )
        template_id = item['id']
        version = item['version']
        if not isinstance(template_id, str) or not template_id:
            raise DeckTransferError(
                f'Trusted-шаблон {index}: неверный ID'
            )
        if template_id in template_ids or version in template_versions:
            raise DeckTransferError('Повтор trusted-шаблона или версии')
        try:
            TemplateProvenance(item['source_provenance'])
            template = TrustedTemplateVersion(
                deck_id='import-source',
                version=version,
                front_source=item['front_source'],
                back_source=item['back_source'],
                source_hash=item['source_hash'],
                provenance=TemplateProvenance.IMPORTED,
                origin_template_id=template_id,
            )
        except (TypeError, ValueError) as error:
            raise DeckTransferError(
                f'Некорректный trusted-шаблон {index}: {error}'
            ) from error
        template_ids.add(template_id)
        template_versions.add(version)
        trusted_templates.append(template)

    if (
        trusted_templates
        and render_settings.authoring_mode is not AuthoringMode.ADVANCED
    ):
        raise DeckTransferError(
            'Trusted-шаблоны допустимы только в Advanced-колоде'
        )
    if trusted_templates:
        create_with_trusted = getattr(
            repo, 'create_deck_with_cards_and_trusted', None
        )
        if create_with_trusted is None:
            raise DeckTransferError(
                'Хранилище не поддерживает безопасный импорт trusted-шаблонов'
            )
        return create_with_trusted(
            name=f'{name.strip()} (импорт)',
            description=description,
            parent_id=source_deck_id,
            cards=CardDeck(cards),
            render_settings=render_settings,
            trusted_templates=tuple(trusted_templates),
        )

    return repo.create_deck_with_cards(
        name=f'{name.strip()} (импорт)',
        description=description,
        parent_id=source_deck_id,
        cards=CardDeck(cards),
        render_settings=render_settings,
    )
