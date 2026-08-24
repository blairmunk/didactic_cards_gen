from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from ..domain.entities import Card, CardDeck, Deck
from ..domain.interfaces import DeckRepository
from ..domain.rendering import AuthoringMode, DeckRenderSettings
from ..domain.trusted import (
    TemplateProvenance,
    TrustedTemplateVersion,
)


DECK_EXPORT_SCHEMA_VERSION = 7
SUPPORTED_DECK_EXPORT_SCHEMAS = {
    1, 2, 3, 4, 5, 6, DECK_EXPORT_SCHEMA_VERSION,
}


class DeckTransferError(ValueError):
    pass


def export_deck_json(repo: DeckRepository, deck_id: str) -> bytes:
    deck = repo.get_deck(deck_id)
    if deck is None:
        raise KeyError(deck_id)
    cards = repo.load_cards(deck_id)
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
                'source': template.source,
                'source_hash': template.source_hash,
                'upper_header': template.upper_header,
                'lower_header': template.lower_header,
                'source_provenance': template.provenance.value,
                'front_content_mode': template.front_content_mode.value,
                'back_content_mode': template.back_content_mode.value,
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
    if repo.get_deck(deck_id) is None:
        raise KeyError(deck_id)
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';', lineterminator='\n')
    writer.writerow(['section', 'front', 'back'])
    for card in repo.load_cards(deck_id).cards:
        writer.writerow([card.section, card.front, card.back])
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
        render_settings = DeckRenderSettings.legacy()
    else:
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
        source_card_id = item.get('id')
        if not isinstance(front, str) or not isinstance(back, str):
            raise DeckTransferError(f'Стороны карточки {index} должны быть строками')
        if not isinstance(section, str):
            raise DeckTransferError(f'Секция карточки {index} должна быть строкой')
        if source_card_id is not None and not isinstance(source_card_id, str):
            raise DeckTransferError(f'ID карточки {index} должен быть строкой')
        if source_card_id and source_card_id in source_ids:
            raise DeckTransferError(f'Повтор ID карточки {index}')
        if source_card_id:
            source_ids.add(source_card_id)
        cards.append(Card(
            front=front,
            back=back,
            section=section,
            parent_id=source_card_id,
        ))

    trusted_templates: list[TrustedTemplateVersion] = []
    if schema_version >= 4:
        template_items = payload.get('trusted_templates')
        if not isinstance(template_items, list):
            raise DeckTransferError(
                'Export schema 4+ должен содержать trusted_templates'
            )
        template_ids: set[str] = set()
        template_versions: set[int] = set()
        expected_fields = {
            'id', 'version', 'source', 'source_hash', 'source_provenance',
            'front_content_mode', 'back_content_mode',
        }
        if schema_version >= 7:
            expected_fields |= {'upper_header', 'lower_header'}
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
                    source=item['source'],
                    upper_header=item.get('upper_header', ''),
                    lower_header=item.get('lower_header', ''),
                    source_hash=item['source_hash'],
                    provenance=TemplateProvenance.IMPORTED,
                    origin_template_id=template_id,
                    front_content_mode=item['front_content_mode'],
                    back_content_mode=item['back_content_mode'],
                )
            except (TypeError, ValueError) as error:
                raise DeckTransferError(
                    f'Некорректный trusted-шаблон {index}: {error}'
                ) from error
            template_ids.add(template_id)
            template_versions.add(version)
            trusted_templates.append(template)

    if trusted_templates:
        render_settings = DeckRenderSettings.from_dict({
            **render_settings.to_dict(),
            'authoring_mode': 'advanced',
        })
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
