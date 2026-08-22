from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from ..domain.entities import Card, CardDeck, Deck
from ..domain.interfaces import DeckRepository
from ..domain.rendering import DeckRenderSettings


DECK_EXPORT_SCHEMA_VERSION = 2
SUPPORTED_DECK_EXPORT_SCHEMAS = {1, DECK_EXPORT_SCHEMA_VERSION}


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

    return repo.create_deck_with_cards(
        name=f'{name.strip()} (импорт)',
        description=description,
        parent_id=source_deck_id,
        cards=CardDeck(cards),
        render_settings=render_settings,
    )
