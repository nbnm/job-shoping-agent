"""Extract a source-linked wardrobe purchase history from the local shopping archive."""

import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from contextlib import ExitStack, closing
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from email.utils import parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

from archive import MAX_MESSAGE_BYTES, PARSER, VisibleText
from wardrobe_purchase_metadata import ReceiptPhotos, exclude_item, key as photo_key, pick_photo


TORONTO = ZoneInfo('America/Toronto')
BRANDS = (
    'Jan & Jul', 'Fjällräven', 'Fjallraven', 'The North Face', 'Helly Hansen', 'New Balance',
    'Under Armour', 'Canada Goose', 'Calvin Klein', 'Massimo Dutti', 'Bandit Running',
    'Arc\'teryx', 'Ciele Athletics', 'Patagonia', 'Tracksmith', 'lululemon', 'Smartwool',
    'Blundstone', 'Birkenstock', 'Icebreaker', 'Brooks', 'Saucony', 'Salomon', 'SAXX',
    'Adidas', 'ASICS', 'Nike', 'HOKA', 'Mack Weldon', 'Vuori', 'Uniqlo', 'Roark', '2XU',
    'Marmot', 'Rab', 'Columbia', 'Merrell', 'Keen', 'Sorel', 'Stance', 'On Running', 'Puma',
    'Chantelle', 'Crocs', 'Injinji', 'Monbessi', 'Superfeet', 'CURREX', 'Lazy One',
    'FURTALK', 'Zingtto', 'GAP', 'Speedo', 'Arena', 'Quechua', 'Kiprun', 'Domyos', 'Kalenji',
    'Van Rysel', 'Simond', 'Forclaz', 'Wedze', 'Kamik', 'Baffin', 'Vans', 'Converse',
    'Coach', 'Kotn', 'Carhartt', 'Aldo', 'Reebok', 'Darn Tough', 'Timberland', 'Oakley',
    'Hanes', 'Fruit of the Loom', 'Champion', 'Levi\'s', 'Wrangler', 'Baleaf', 'Physix Gear',
    'Dickies', 'BUFF', 'DPKOW', 'BTFBM', 'Mizuno', 'Spooktacular Creations', 'Teehee', 'toolant', 'JanSport',
    'oodji', 'Topman', 'Quiksilver',
)
RETAILERS = {
    'lululemon': ('lululemon', 'lulu'), 'decathlon': ('Decathlon', 'decathlon'),
    'adidas': ('Adidas', 'adidas'), 'nike': ('Nike', 'nike'), 'tracksmith': ('Tracksmith', 'shopify'),
    'banditrunning': ('Bandit Running', 'shopify'), 'newbalance': ('New Balance', 'nb'),
    'sportchek': ('Sport Chek', 'sportchek'), 'sportinglife': ('Sporting Life', 'sportinglife'),
    'uniqlo': ('Uniqlo', 'uniqlo'), 'vuoriclothing': ('Vuori', 'vuori'), 'mec': ('MEC', 'mec'),
    'blacktoerunning': ('BlackToe Running', 'blacktoe'), 'canadagoose': ('Canada Goose', 'goose'),
    'arcteryx': ("Arc'teryx", 'arcteryx'), 'patagonia': ('Patagonia', 'patagonia'),
    'thenorthface': ('The North Face', 'tnf'), 'calvinklein': ('Calvin Klein', 'ck'),
    'roark': ('Roark', 'shopify'), 'saxxunderwear': ('SAXX', 'saxx'), 'hoka': ('HOKA', 'hoka'),
    'massimodutti': ('Massimo Dutti', 'md'), 'mackweldon': ('Mack Weldon', 'shopify'),
    'asics': ('ASICS', 'asics'), 'coach': ('Coach', 'coach'), 'cieleathletics': ('Ciele Athletics', 'ciele'),
    'ascentcycle': ('Ascent Cycle', 'shopify'), 'seesawyerrun': ('See Sawyer Run', 'shopify'),
    'altitude-sports': ('Altitude Sports', 'shopify'), 'petitlem': ('Petit Lem', 'shopify'),
    'underarmour': ('Under Armour', 'shopify'), 'ssense': ('SSENSE', 'shopify'),
    'strathberry': ('Strathberry', 'shopify'), 'zacposen': ('Zac Posen', 'shopify'),
    'sorel': ('Sorel', 'shopify'), 'saucony': ('Saucony', 'shopify'),
    'runningroom': ('Running Room', 'runningroom'), 'lamoda': ('Lamoda', 'lamoda_ru'),
}
DISPLAY_STORES = {'tracksmith': ('Tracksmith', 'shopify'), 'bandit running': ('Bandit Running', 'shopify'),
                  '2xu canada': ('2XU Canada', 'shopify'), 'le club': ('Le Club', 'shopify'),
                  'blacktoe running inc.': ('BlackToe Running', 'blacktoe'),
                  'blacktoe running': ('BlackToe Running', 'blacktoe')}
HOUSE_BRANDS = dict((store, store) for store in ('lululemon', 'Adidas', 'Nike', 'Tracksmith', 'Bandit Running',
    'New Balance', 'Uniqlo', 'Vuori', 'Canada Goose', "Arc'teryx", 'Patagonia', 'The North Face',
    'Calvin Klein', 'Roark', 'SAXX', 'HOKA', 'Massimo Dutti', 'Mack Weldon', 'ASICS', 'Coach',
    'Ciele Athletics', 'Under Armour', 'Sorel', 'Saucony', 'Strathberry', 'Zac Posen'))
HOUSE_BRANDS['2XU Canada'] = '2XU'
HOUSE_BRANDS['On Running'] = 'On Running'

CATEGORY = [(category, re.compile(pattern, re.I)) for category, pattern in (
    ('Shoes', r'\b(?:shoes?|sneakers?|sandals?|slippers?|clogs?|loafers?|footwear|moccasins?|'
              r'(?:hiking|winter|rain|snow|chelsea|work|ankle) boots?|boots|slides?|flip[ -]?flops|'
              r'fresh foam|novablast|gel-kayano|gel-nimbus|gel-cumulus|solarglide|ultraboost|stornglide|'
              r'fuelcell|adizero|speedgoat|clifton \d|bondi \d|air zoom|air max|pegasus \d|ora recovery)\b'),
    ('Outerwear', r'\b(?:jacket|windbreaker|raincoat|coat|parka|puffer|poncho|shell jacket|'
                  r'electron pro hoody|borod full zip|track top|down vest|fleece full-zip)\b'),
    ('Bottoms', r'\b(?:jeans|trousers?|(?:sweat)?pants?|joggers?|leggings?|tights?|shorts|running short|2-in-1 short|skirts?|trunks?|'
                r'underwear|boxer briefs?|boxers?|briefs?|hipsters?|thongs?|panties|jammer|'
                r'swim bottom|pantalon|collant)\b'),
    ('Knitwear', r'\b(?:sweaters?|cardigans?|knit polo)\b'),
    ('Tops', r'\b(?:shirts?|t[ -]?shirts?|tee(?:s)?|tshirt|hood(?:ie|y|ies)|sweatshirts?|'
             r'singlets?|tanks?|tanktop|blouses?|dress(?:es)?|leotards?|bodysuits?|onesies?|'
             r'robe|bralette|bras?|polo|rash ?guard|swimsuits?|bikini|crewneck|crew neck|'
             r'jerseys?|base layer|trackhouse crew|dotknit|pullovers?|running top|ts\s?shirt|nsb(?:tank|tshirt))\b'),
    ('Accessories', r'\b(?:socks?|beanies?|hats?|sun[ -]?hats?|caps?|headbands?|mittens?|'
                    r'gloves?|scarves|scarf|belt|visors?|balaclava|\bstance unisex ultra tab)\b'),
)]
NON_FASHION = re.compile(r'\b(?:gift cards?|membership|insoles?|orthotics?|shoe laces?|boot tray|'
                        r'shoe cleaner|shoe(?:s)? (?:rack|bag|organizer|deodori[sz]er|brush)|'
                        r'(?:sock|wardrobe|clothes) (?:drawer|organizer|hanger|tag remover)|'
                        r'(?:dishwashing|cleaning|food handling|vinyl|nitrile|disposable|gardening|'
                        r'latex|rubber) gloves?|disposable (?:plastic |food |clear )*gloves?|glove liners? for cleaning|glove box|'
                        r'(?:doll|toy|puppet|figure)\b.{0,70}\b(?:clothes|outfit|dress|accessories)|'
                        r'(?:doll|toy|mug|bottle|grease tray|helmet|hard hat|hub cap|baseball bat|'
                        r'watch band|filter cartridge|screw cap|stem cap|swimming cap holder|'
                        r'water bottle|shoe horn|hangers?|storage bags?|garment bags?|rain cover|'
                        r'pillow|throw blanket|bike trailer|frame bag|floor mat|door mat|trash bag|'
                        r'wall hook|seam ripper|inflatable|cake topper|party supplies|pet costume|'
                        r'cookie cutters?|crib mattress|outlet plugs?|electrical protector|wax strips?|'
                        r'foam (?:princess )?crowns?|artificial green leaf crown|floaties|swim(?:ming)? vest|boxing gloves?|'
                        r'book covers?|drawstring garment bags?)|'
                        r'\b(?:shoe|sneaker|hat|cap|pants)\s+(?:charms?|storage|holder|protector|display)\b)\b', re.I)
AMAZON_ID = re.compile(r'(?<!\d)\d{3}-\d{7}-\d{7}(?!\d)')
NUMBER = r'(?:[A-Z0-9]+[-_/])*[A-Z0-9]+'
ORDER_NUMBER = re.compile(rf'\border\s*(?:number|no\.?|n[º°.]|id|\#|confirmation\s*[:#]|(?=[A-Z]*\d{{5}}))\s*[:#(]*\s*({NUMBER})', re.I)
AMOUNT = re.compile(r'(?<![-\d.,])(?:(CAD|CDN|USD|EUR|GBP)\s*\$?\s*|(CA\$|C\$+|CDN\$|US\$|\$|€|£)\s*)'
                    r'(\d+(?:,\d{3})*(?:\.\d+)?)(?:\s*(CAD|CDN|USD|EUR|GBP)\b)?|'
                    r'(?<![-\d.,])(\d+(?:,\d{3})*(?:\.\d+)?)\s+(CAD|CDN|USD|EUR|GBP)\b', re.I)
MONTH = r'(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)'
DATE_TEXT = rf'(?:\d{{4}}-\d{{2}}-\d{{2}}|{MONTH}\.?\s+\d{{1,2}},?\s+\d{{4}}|\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{2}})'
DATE_LABEL = re.compile(rf'(?:\border\s+(?:date|placed\s+on|placed)|\bplaced\s+on|\breceived:)\s*[:| ]*\s*'
                        rf'(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*)?({DATE_TEXT})', re.I)
NON_ORDER = re.compile(r'\b(?:feedback|review|survey|signing up|newsletter|marathon excellence|payment for|track your order with shop)\b', re.I)
RECEIPT = re.compile(r'\b(?:invoice|(?:e-?|digital |your |store )?receipt)\b', re.I)
CONFIRM = re.compile(r'^(?:ordered:|confirmed!|your amazon\.[a-z.]+ order(?:\s+#?[\d-]+\s+of\b|\s*$))|\b(?:order\b.{0,55}\b(?:confirmed|received|acknowledgement)|'
                     r'(?:confirm(?:ation)? (?:of |for )?(?:(?:new|your|the) )?order)|order confirmation|'
                     r'(?:we got|thanks for|thank you for) your (?:order|purchase))\b', re.I)
RETURN_QUALIFIER = re.compile(r'\b(?:return(?:s|ed|ing)?|refund(?:s|ed|ing)?|exchange)\b', re.I)
SIZE = re.compile(r'^(?:\(?\s*(?:US|UK|EU|CA|W|M)\s*)?(?:\d{1,3}(?:[.,]\d+)?(?:\s*(?:(?:W|L|M|D|B|2E|4E)\b|[-–/x]\s*(?:\w+\s*)?\d+[\w/ .-]*))?|'
                  r'\d{1,3}(?:[.,]\d+)?\s*(?:Toddler|Little Kid|Big Kid|cm|months?|yrs?|years?|T)|'
                  r'X{0,4}[SML]|[2345]X[LS]|XS/S|S/M|M/L|L/XL|W\d+\s*[x/]\s*L\d+|'
                  r'one size(?: fits all)?|\b[234]-?pack\b|XX[- ]?(?:SMALL|LARGE)|X[- ]?(?:SMALL|LARGE)|'
                  r'small|medium|large|extra large|OS(?:FA)?)(?:\s*\(.*\))?$', re.I)
RETURN_EVENTS = {'return_requested', 'returned', 'refunded', 'cancelled'}
ORDER_EVENTS = {'confirmation', 'receipt'}
RETURN_REFERENCE = re.compile(r'\b(?:RET[A-Z]{2}\d{4,}|RMA[- ]?\d{4,})\b', re.I)
REVIEWABLE_FAMILIES = {'lulu','adidas','nike','shopify','nb','sportchek','sportinglife','uniqlo',
                       'vuori','mec','blacktoe','goose','arcteryx','patagonia','tnf','ck','saxx',
                       'hoka','md','asics','coach','ciele','on'}
PAYMENT_PRIORITY = {'receipt': 5,'confirmation': 4,'shipment': 2,'delivered': 1,'update': 0}
RETURN_RANK = {'no_return_evidence': 0, 'return_requested': 1, 'returned': 2, 'refunded': 3, 'cancelled': 3}


@dataclass
class Line:
    name: str
    size: str | None = None
    color: str | None = None
    quantity: int = 1
    price_minor: int | None = None
    currency: str | None = None
    sku: str | None = None
    brand: str | None = None
    category: str | None = None
    audience: str | None = None


@dataclass
class Evidence:
    seq: int
    message_id: str
    merchant: str
    number: str | None
    event: str
    sent_at: str | None
    on: date | None
    explicit_date: date | None
    lines: list[Line] = field(default_factory=list)
    link_method: str = 'order_number'
    whole_order: bool = False
    reference: str | None = None
    body_source: str = 'sqlite_text'
    order_only: bool = False


@dataclass
class Order:
    id: str
    merchant: str
    number: str | None
    events: list[Evidence] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    purchased_on: date | None = None
    purchase_basis: str | None = None
    purchase_seq: int | None = None
    first_seen: date | None = None
    confirmed_full_item_count: int | None = None
    unassigned_returns: int = 0


def namekey(value):
    value = unicodedata.normalize('NFKD', value or '').casefold()
    return ''.join(c for c in value if c.isalnum())


def display(value):
    return ' '.join((value or '').split()).strip(' |\t')


def sanitize_text(body):
    body = html.unescape(body)
    body = re.sub(r'[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]', '', body)
    body = re.sub(r'\[([^\[\]\n]+)\]\(https?://[^\n]*', r'\1', body)
    body = re.sub(r'https?://[^\s<>|]+', '', body)
    body = re.sub(r'<br\s*/?>', ' ', body, flags=re.I)
    body = re.sub(r'</?[A-Za-z][^>\n]*>', '', body)
    return body


def lines_for(body):
    return [l for s in body.splitlines() if (l := display(s)) and l not in {'()', '( )', '(', ')'}]


def currency_code(value):
    if value in ('CA$', 'C$', 'C$$', 'CDN$', 'CDN'):
        return 'CAD'
    return {'US$': 'USD', '€': 'EUR', '£': 'GBP', '$': None}.get(value, value)


def amount(value, inherited=None):
    m = AMOUNT.search(value)
    if not m:
        return None, None
    minor = int((Decimal((m[3] or m[5]).replace(',', '')) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    code = (m[4] or m[6] or m[1] or m[2] or '').upper()
    return minor, currency_code(code) or inherited


def money_line(value):
    return bool(AMOUNT.fullmatch(re.sub(r'^(?:PRICE|MSRP|VALUE|TOTAL)\s*:?\s*', '', value, flags=re.I)))


def currency_context(text, merchant, domain):
    found = {currency for m in AMOUNT.finditer(text) if (currency := amount(m[0])[1])}
    if len(found) == 1:
        return next(iter(found))
    if not found and (domain.endswith('.ca') or re.search(r'\b(?:GST/HST|HST-ON|HSTON)\b', text, re.I)
                      or (re.search(r'(?m)^\s*Canada\s*$', text, re.I) and re.search(r'\b(?:GST|HST)\b', text, re.I))):
        return 'CAD'
    return None


def retailer(sender, body):
    name, email = parseaddr(sender)
    domain = email.rpartition('@')[2].lower()
    if re.search(r'(?:^|\.)(?:on\.com|on-running\.com)$', domain):
        return 'On Running', 'on', domain
    if domain == 'adidas.ru' or domain.endswith('.adidas.ru'):
        return 'Adidas', 'adidas_ru', domain
    if domain.endswith('.shopbop.com') and re.search(r'\bEast Dane\b', body, re.I):
        return 'East Dane', 'eastdane', domain
    if re.search(r'(?:^|\.)amazon\.(?:ca|com|de|co\.uk)$', domain):
        # One order key across Amazon transactional senders within a storefront.
        return ('Amazon.' + domain.split('amazon.')[-1], 'amazon', domain)
    if domain.endswith(('shopifyemail.com', 'returngo.ai')):
        return (*DISPLAY_STORES.get(name.lower(), (name or None, 'shopify')), domain)
    for token in domain.split('.'):
        if token in RETAILERS:
            return (*RETAILERS[token], domain)
    return None, None, domain


def event_type(subject, body, template):
    s = subject.strip()
    if not s or re.match(r'^(?:re|fw|fwd):', s, re.I) or NON_ORDER.search(s):
        return None
    if template in {'adidas_ru','lamoda_ru'}:
        for pattern, kind in ((r'оформлен|подтвержд[её]н', 'confirmation'), (r'отправлен|отгружен|передан', 'shipment'),
                              (r'доставлен', 'delivered'), (r'отмен[её]н', 'cancelled')):
            if re.search(r'\bзаказ\b.*\b(?:' + pattern + r')\b', s, re.I):
                return kind
    if template == 'runningroom' and re.search(r'^Running Room order\s*#', s, re.I):
        return 'confirmation'
    if re.search(r'\b(?:cancelled|canceled|your cancellation)\b', s, re.I):
        return 'cancelled'
    if RETURN_QUALIFIER.search(s):
        if re.search(r'\b(?:refund(?:ed)?|credit confirmation)\b', s, re.I):
            return 'refunded'
        if re.search(r'\b(?:processed|received your (?:\w+ )?return(?! request)|received your returned|'
                     r'receipt|returned items|return is complete|return (?:has been )?(?:received|processed|completed))\b', s, re.I):
            return 'returned'
        if re.search(r'\b(?:approved|label|exchange)\b', s, re.I):
            return 'return_requested'
        if re.search(r'\b(?:return|exchange)\b', s, re.I):
            return 'return_requested'
    if re.search(r'\b(?:delivered|waiting game is over|delivery confirmed|order is here|order has arrived|pick ?up completed)\b', s, re.I):
        return 'delivered'
    if re.search(r'\b(?:on (?:its|the) way|on the move|shipped|dispatched|shipment|tracking|'
                 r'out for delivery|in transit|arrives? tomorrow|arriving|en route|ready for pick ?up)\b', s, re.I):
        return 'shipment'
    if CONFIRM.search(s):
        return 'confirmation'
    if RECEIPT.search(s):
        return 'receipt'
    if template in {'lulu', 'nb', 'tnf'} and re.search(r'order|package', s, re.I):
        return 'update'
    return None


def order_number(subject, body, family):
    if family == 'adidas_ru' and (m := re.search(r'\bARU\d{5,}\b', subject + '\n' + body, re.I)):
        return m[0].upper()
    if family == 'lamoda_ru' and (m := re.search(r'\bRU\d{5,}-\d{3,}\b', subject + '\n' + body, re.I)):
        return m[0].upper()
    if family == 'lulu':
        m = re.search(r'\bc[a-z]{0,2}\d{10,20}\b', subject + '\n' + body, re.I)
        if m:
            return m[0].upper()
    if family in {'nike', 'adidas', 'hoka', 'asics'}:
        patt = {'nike': r'G\d{8,}', 'adidas': r'AC[A-Z]\d{6,}', 'hoka': r'NA\d{7,}',
                'asics': r'ASCCA\d{6,}'}[family]
        m = re.search(r'\b' + patt + r'\b', subject + '\n' + body, re.I)
        if m:
            return m[0].upper()
    for text in (subject, body):
        for m in ORDER_NUMBER.finditer(text):
            value = m[1].strip('-_/').upper()
            if len(value) >= (3 if family in {'shopify','blacktoe'} else 4) and re.search(r'\d', value):
                return value
        if family in {'shopify', 'blacktoe', 'ciele', 'patagonia', 'ck', 'tnf', 'arcteryx', 'coach'}:
            m = re.search(r'\b(?:order(?:\s+(?:confirmation|details?))?\s*[:#]?\s*|(?:order\s+.*?)#)'
                          r'([A-Z]{0,4}\d{5,})\b', text, re.I)
            if m:
                return m[1].upper()
    return None


def parse_date(text, family, on):
    m = DATE_LABEL.search(text[:24000])
    if not m and family == 'on':
        m = re.search(r'\border date\s*[:|\s]*(\d{1,2}\.\d{1,2}\.\d{4})\b', text[:24000], re.I)
    if not m and family in {'shopify', 'blacktoe', 'ciele'}:
        m = re.search(rf'\bOrder\s*(?:No\.?\s*)?#?{NUMBER}\s*\)?\s*\(?({DATE_TEXT})', text, re.I)
    if not m:
        if family == 'ck' and on and (short := re.search(rf'\bORDER DATE\s+(?:\w{{3}},?\s+)?(\d{{1,2}}\s+{MONTH})\b', text, re.I)):
            for year in (on.year,on.year-1):
                for form in ('%d %b %Y','%d %B %Y'):
                    try:
                        day = datetime.strptime(f'{short[1]} {year}', form).date()
                        if -1 <= (on-day).days <= 31:
                            return day
                    except ValueError:
                        pass
        return None
    value = m[1].replace('.', '/') if family == 'on' and re.fullmatch(r'\d{1,2}\.\d{1,2}\.\d{4}', m[1]) else m[1].title().replace('Sept ', 'Sep ').replace('.', '')
    formats = ['%Y-%m-%d', '%B %d, %Y', '%B %d %Y', '%b %d, %Y', '%b %d %Y']
    if family == 'mec':
        formats += ['%y/%m/%d']
    formats += ['%d/%m/%Y', '%m/%d/%Y'] if family == 'nb' else ['%m/%d/%Y', '%d/%m/%Y']
    formats += ['%m-%d-%Y', '%d-%m-%Y']
    candidates = []
    for fmt in formats:
        try:
            day = datetime.strptime(value, fmt).date()
            if day.year >= 2010 and (on is None or -1 <= (on - day).days <= 365):
                candidates.append(day)
        except ValueError:
            pass
    return min(candidates, key=lambda d: abs((on-d).days)) if on and candidates else next(iter(candidates), None)


def category_for(name, brand=None, family=None):
    if NON_FASHION.search(name):
        return None
    if brand == 'On Running' and re.search(r'\bCloud(?:runner|surfer|monster|stratus|flyer|flow|swift|vista|ultra|venture|boom|tilt|pulse|away|nova|x|go|\s+\d+)\b', name, re.I):
        return 'Shoes'
    if family == 'saxx' and re.search(r'\b(?:ultra|vibe|quest|kinetic|daytripper)\b', name, re.I):
        return 'Bottoms'
    if family in {'adidas_ru','lamoda_ru'}:
        for pattern, cat in ((r'сланцы|кроссовки|кеды|сандалии|ботинки', 'Shoes'),
                             (r'ветровка|куртка|пальто', 'Outerwear'), (r'футболка|майка|рубашка|свитшот', 'Tops'),
                             (r'джемпер|свитер', 'Knitwear'), (r'брюки|шорты|трусы|комплект трусов', 'Bottoms'),
                             (r'подтяжки', 'Accessories')):
            if re.match(pattern + r'\b', name, re.I):
                return cat
    if family == 'eastdane' and re.search(r'\bbackpack\b', name, re.I):
        return 'Accessories'
    if re.search(r'\b(?:ballet leotard|bikini top|swim(?:ming)? suit)\b', name, re.I):
        return 'Tops'
    return next((category for category, rx in CATEGORY if rx.search(name)), None)


def brand_for(name, merchant):
    if re.search(r'\b(?:Montana Futurelight|Salty Pup Beanie)\b', name, re.I):
        return 'The North Face'
    for brand in BRANDS:
        if re.search(r'(?<!\w)' + re.escape(brand) + r'(?!\w)', name, re.I):
            return {'Fjallraven': 'Fjällräven'}.get(brand, brand)
    return HOUSE_BRANDS.get(merchant)


def size_for_title(name):
    tail = re.split(r'[,;(]', name)[-1].strip(' ).')
    if SIZE.fullmatch(tail):
        return tail
    if match := re.search(r'\bSize\s*[: ]\s*(\d+(?:\.\d+)?(?:\s*(?:US|UK|EU|Toddler))?|XXL|XL|XS|S|M|L|One Size)\b', name, re.I):
        return match[1]
    if match := re.search(r'\b(\d{2,3}[–-]\d{2,3}\s*cm|[1-9]\d?(?:\s*[–-]\s*\d+)?\s*(?:Toddler|Months|Years?)|\d{1,2}(?:[–-]\d{1,2})?T)\b', tail, re.I):
        return match[1]
    if match := re.search(r'\((\d{1,2}[–-]\d{1,2}),\s*[^)]+\)\s*$', name):
        return match[1]
    return None


def line(name, variant=None, color=None, qty=1, price='', cur=None, sku=None, brand=None):
    name = display(name).strip('* |–')
    if not name or len(name) > 450 or len(name) < 3 or not re.search(r'[^\W\d_]', name):
        return None
    if re.match(r'^(?:Subtotal|Order Total|Total Saving|Price|Order Summary|Item\(s\)|Size|Color|Shipping|Discount)\b', name, re.I):
        return None
    variant = display(variant).strip(' /:') or None
    if variant and (variant.lower() in ('no size', '.', 'n/a', 'default title', 'fully fuelled') or len(variant) > 60):
        variant = None
    minor, stated = amount(price, cur)
    return Line(name, variant, display(color) or None, max(1, abs(int(qty))), minor, stated, display(sku) or None, brand)


def dedup_lines(items):
    found = {}
    for item in filter(None, items):
        key = (namekey(item.name), namekey(item.size), namekey(item.color), namekey(item.sku), item.audience)
        old = found.get(key)
        if not old or (old.price_minor is None and item.price_minor is not None):
            found[key] = item
    return list(found.values())


def field_value(block, label):
    rx = re.compile(r'^(?:' + label + r')\s*:?\s*(.*)$', re.I)
    for i, text in enumerate(block):
        if m := rx.match(text):
            return m[1].strip() or (block[i+1] if i+1 < len(block) else None)
    return None


def qty_value(block):
    v = field_value(block, r'qty\.?|quantity')
    return int(m[0]) if v and (m := re.search(r'-?\d+', v)) and abs(int(m[0])) < 1000 else 1


def previous_name(lines, i, limit=6):
    for l in reversed(lines[max(0, i-limit):i]):
        if (not money_line(l) and not re.fullmatch(r'[-–*() .]+|[\d/.-]+', l)
                and not re.match(r'^(?:colou?r|qty|quantity|size|sku|style|item no|article no|price|msrp|gender)\s*:', l, re.I)
                and re.search(r'[A-Za-z]', l)):
            return l
    return ''


def amazon_sections(subject, body, cur, event):
    # A single confirmation can contain several separate Amazon orders.
    headers = list(re.finditer(r'(?im)^\s*Order\s*#\s*(' + AMAZON_ID.pattern + r')', body))
    found = defaultdict(list)
    if not headers:
        m = AMAZON_ID.search(subject) or AMAZON_ID.search(body)
        if m:
            found[m[0]] = []
            sections = [(m[0], body)]
        else:
            return []
    else:
        sections = [(m[1], body[m.end(): headers[i + 1].start() if i + 1 < len(headers) else len(body)])
                    for i, m in enumerate(headers)]
    for number, section in sections:
        items = found[number]
        lines = lines_for(section)
        for i, l in enumerate(lines):
            if m := re.fullmatch(r'(?:Quantity|Qty)\s*:\s*(\d+)', l, re.I):
                if not i:
                    continue
                title = re.sub(r'^Item:\s*', '', lines[i-1].lstrip('* '), flags=re.I)
                if title in {'Item details:', 'Item(s) to be returned:', 'Product details:'}:
                    continue
                price = lines[i+1] if i+1 < len(lines) and money_line(lines[i+1]) else ''
                asin = lines[i+1][5:].strip() if i+1 < len(lines) and lines[i+1].startswith('ASIN:') else None
                items.append(line(title, size_for_title(title), qty=int(m[1]), price=price, cur=cur, sku=asin))
        if any(items):
            continue
        # Older confirmations list names after "Item(s) Subtotal" or after an Amazon product link.
        for l in lines:
            if m := re.fullmatch(r'(?:Item(?:s)?(?: details)?\s*:\s*)?([^|]{8,300})\s+\|\s*(\d{1,2})\s+\|\s*(.+)', l, re.I):
                if money_line(m[3]):
                    items.append(line(m[1], size_for_title(m[1]), qty=int(m[2]), price=m[3], cur=cur))
        for i, l in enumerate(lines[1:-1],1):
            if money_line(l) and re.match(r'^Sold by\b',lines[i+1],re.I):
                name = lines[i-1]
                items.append(line(name,size_for_title(name),price=l,cur=cur))
        if event in RETURN_EVENTS:
            for i, l in enumerate(lines[:-1]):
                if re.fullmatch(r'Item details:', l, re.I):
                    title = lines[i+1]
                    items.append(line(title, size_for_title(title), cur=cur))
    if len(found) == 1 and not any(next(iter(found.values()))):
        titles = (re.search(r'[“"]([^”"]{5,300})[”"]', subject)
                  or re.search(r'\b(?:refund for|return of) (.{7,180})', subject, re.I))
        if titles and (event in RETURN_EVENTS or not re.search(r'\.\.\.|…', titles[1])):
            item = line(titles[1], size_for_title(titles[1]), cur=cur)
            if item:
                next(iter(found.values())).append(item)
    dates = {number: parsed for number, section in sections if (parsed := parse_date(section, 'amazon', None))}
    return [(key, dedup_lines(items), dates.get(key)) for key, items in found.items()]


def shopify_lines(lines, cur, family, refunded_only=False):
    # Shopify may insert a discount name and negative amount between a variant and its quantity.
    lines = [l for i, l in enumerate(lines) if not (
        re.fullmatch(r'\(-\$[\d,.]+\)', l) or
        (i+1 < len(lines) and re.fullmatch(r'\(-\$[\d,.]+\)', lines[i+1])
         and re.search(r'^\d+\s+FOR\s+\$', l, re.I))
    )]
    out = []
    for i, l in enumerate(lines):
        m = re.fullmatch(r'(.*?)\s*[×x]\s*(\d{1,2})', l)
        if not m:
            continue
        name, qty = m.groups()
        if not name and i >= 2:
            variant = lines[i-1]
            explicit_brand = None
            start = i-2
            if '/' not in variant and not SIZE.fullmatch(variant):
                # Le Club explicitly names the label ahead of a discount and quantity.
                for j in range(i-1, max(-1, i-7), -1):
                    if SIZE.fullmatch(lines[j]):
                        variant, start = lines[j], j-1
                        if j+1 < i and (known := brand_for(lines[j+1], None)):
                            explicit_brand = known
                        break
                else:
                    variant = ''
            name = previous_name(lines, start+1, 3)
            if re.search(r'\b(?:Discount|Refunded)\b', name, re.I) or (variant and name == variant):
                continue
        else:
            explicit_brand = None
            variant = lines[i+1] if i+1 < len(lines) else ''
            if (money_line(variant) or re.search(r'^(?:Refunded|Subtotal|Discount|Final Sale|Free)\b', variant, re.I)):
                variant = ''
        if not name or re.match(r'^(?:styling|mso-|max-|line-height|font-|[A-Za-z]+:|size)\b', name, re.I):
            continue
        name = re.sub(r'^(?:Men|Women|All-Gender|Accessories)\s+-\s+', '', name, flags=re.I)
        if family == 'blacktoe' and i > 0 and re.match(r'^(?:\d+[\d.]*|[XSML]+)\s*/', variant):
            parts = variant.split(' / ', 1)
        else:
            parts = variant.split(' / ')
        if family == 'blacktoe' and parts and SIZE.fullmatch(parts[0]):
            size = parts[0]
            color = parts[1] if len(parts)>1 else None
        else:
            sizes = [p for p in parts if SIZE.fullmatch(p)]
            size = sizes[0] if sizes else None
            color = (parts[0] if len(parts)>1 and parts[0] != size else
                     parts[1] if len(parts)>1 and parts[1] != size else None)
        if not size:
            size = size_for_title(name)
        prices = []
        following = []
        for l2 in lines[i+1:i+12]:
            if re.search(r'(?:[×x]\s*\d+\s*$|^subtotal)',l2,re.I):
                break
            following.append(l2)
        if refunded_only and not any(t.casefold() == 'refunded' for t in following):
            continue
        for l2 in following[:7]:
            if money_line(l2) or l2.casefold() == 'free':
                prices.append(l2 if money_line(l2) else '$0.00')
            elif re.search(r'(?:×\s*\d+|^subtotal|^shipping|^taxes?|^total$|^discount$)', l2, re.I) or (prices and not money_line(l2)):
                break
        out.append(line(name, size, color, qty, prices[-1] if prices else '', cur, brand=explicit_brand))
    return out


def retailer_lines(body, family, cur, event):
    lines = lines_for(body)
    out = []
    if family == 'on':
        lines = [part for part in lines if not re.fullmatch(r'[-–—]{3,}', part)]
        for i, part in enumerate(lines):
            if i < 2 or not (match := re.fullmatch(r'Size:\s*(.*)', part, re.I)):
                continue
            attrs = lines[i+1:i+8]
            size = match[1] or (attrs.pop(0) if attrs else '')
            if not SIZE.fullmatch(size):
                continue
            audience = re.search(r"\((Men|Women|Kids)(?:['’]s)?\)$", size, re.I)
            if audience:
                size = size[:audience.start()].strip()
            # Keep the item price separate from the receipt's order subtotal.
            attrs = attrs[:next((j for j, v in enumerate(attrs) if re.match(r'^(?:sub\s*total|total|shipping|shipped)$', v, re.I)), len(attrs))]
            price = next((v for v in attrs if money_line(v)), '')
            if not price:
                price = next((f'{v} {attrs[j+1]}' for j,v in enumerate(attrs[:-1])
                              if re.fullmatch(r'CAD|USD|EUR|GBP', v, re.I) and money_line(f'{v} {attrs[j+1]}')), '')
            item = line(lines[i-2], size, lines[i-1], qty_value(attrs), price, cur)
            if item:
                item.audience = audience[1].casefold() if audience else None
                out.append(item)
        return dedup_lines(out)
    if family in {'shopify', 'blacktoe'}:
        partial = event == 'refunded' and 'some items in your order have been refunded' in body.casefold()
        explicitly_marked = bool(re.search(r'(?m)^\s*Refunded\s*$',body,re.I))
        out += shopify_lines(lines, cur, family, refunded_only=partial and explicitly_marked)
    if family == 'runningroom':
        for i, l in enumerate(lines):
            if l.casefold() == 'sku' and i and len(lines) > i+5:
                sku, color, size, qty, price = lines[i+1:i+6]
                if (re.fullmatch(r'[A-Z0-9-]{4,}', sku, re.I) and color.startswith('Color ')
                        and size.startswith('Size ') and qty.isdecimal() and money_line(price)):
                    name = lines[i-1]
                    brand = 'On Running' if re.match(r'^On Unisex\b',name) else (
                        'Running Room' if name.startswith('Running Room Unisex ') else None)
                    out.append(line(name,size[5:],color[6:],qty,price,cur or 'CAD',sku,brand))
        return dedup_lines(out)
    if family == 'eastdane':
        for i, l in enumerate(lines):
            if i < 3 or not re.fullmatch(r'[A-Z]{4,8}\d{8,}', lines[i-1]):
                continue
            if (m := re.fullmatch(r'(\d+)\s*\|\s*([^|]+)\|\s*([^|]+)', l)) and money_line(m[3]):
                name, color, size = (lines[i-2].rsplit(', ', 2) + ['', ''])[:3]
                brand = lines[i-3]
                out.append(line(name,size,color,m[1],m[3],cur,lines[i-1],brand))
        return dedup_lines(out)
    if family == 'lamoda_ru':
        for i, l in enumerate(lines):
            if not 0 < i < len(lines)-3 or not l.startswith('Размер '):
                continue
            sku, qty, price = lines[i+1:i+4]
            if (sku.startswith('Арт. ') and (q := re.fullmatch(r'Кол-во:\s*(\d+)', qty))
                    and (m := re.fullmatch(r'Цена:\s*([\d ]+\.\d{2})\s*руб\.', price))):
                size = l[7:].replace(',', '.')
                out.append(line(lines[i-1], None if size == '00' else size, qty=q[1],
                                price='$' + m[1].replace(' ', ''), cur='RUB', sku=sku[5:]))
        return dedup_lines(out)
    if family == 'adidas_ru':
        for i, l in enumerate(lines):
            if l.casefold() != 'артикул:' or not i or not lines[i-1].startswith('Цвет:'):
                continue
            name = lines[i-2] if lines[i-2] != '- Распродажа!' else lines[i-3]
            for j in range(i+2, min(i+14,len(lines)-2)):
                size, qty, price = lines[j:j+3]
                if SIZE.fullmatch(size) and qty.isdecimal() and (m := re.fullmatch(r'([\d .]+)\s*р\.',price)):
                    out.append(line(name,size,lines[i-1][5:],qty,
                                    '$' + re.sub(r'[^\d]', '', m[1]),'RUB',lines[i+1]))
                    break
        return dedup_lines(out)
    for i, l in enumerate(lines):
        before, after = lines[max(0,i-7):i], lines[i+1:i+9]
        if family == 'lulu' and (m := re.fullmatch(r'(\d+)\s*[x×]\s+(.+?)\s*/\s*Size\s+(.+)', l, re.I)) and before:
            sku = after[0] if after and re.fullmatch(r'\d{6,12}',after[0]) else None
            prices = [v for v in after[bool(sku):bool(sku)+2] if money_line(v)]
            out.append(line(before[-1],m[3],m[2],m[1],min(prices,key=lambda v:amount(v)[0]) if prices else '',cur,sku))
        elif family == 'lulu' and (m := re.fullmatch(r'Size:\s*(.+?)\s+Qty:\s*(\d+)', l, re.I)) and len(before) >= 2:
            sku = after[0] if after and re.fullmatch(r'\d{6,12}',after[0]) else None
            prices = [v for v in after[bool(sku):bool(sku)+2] if money_line(v)]
            out.append(line(before[-2],m[1],before[-1],m[2],min(prices,key=lambda v:amount(v)[0]) if prices else '',cur,sku))
        elif family == 'lulu' and (m := re.fullmatch(r'(.*?)\s*/\s*Size\s+(.+)', l, re.I)):
            j = 1 if m[1] else 2
            q = re.fullmatch(r'(\d+)\s*[x×]', before[-j]) if len(before) >= j else None
            if q and len(before) > j:
                sku = after[0] if after and re.fullmatch(r'\d{6,12}', after[0]) else None
                prices = []
                for t in after[bool(sku):]:
                    if t.lower().startswith('return reason:'):
                        continue
                    if not money_line(t):
                        break
                    prices.append(t)
                out.append(line(before[-j-1], m[2], m[1] or before[-1], q[1],
                                min(prices,key=lambda v:amount(v)[0]) if prices else '', cur, sku))
        elif family == 'uniqlo':
            if m := re.match(r'^\d+\s*\|\s*(\d{5,})\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(\d+)\s*\|\s*([^|]+)\|\s*([^|]+)$', l):
                out.append(line(m[2], m[5], m[6], m[4], m[3], cur, m[1]))
        elif family in {'ck', 'tnf'}:
            if m := re.match(r'^(.+?)\s+(CAD\s*\$?\s*\d[\d,.]*)\s+Quantity:\s*(\d+)\s+Colou?r:\s*(.+?)\s+Size:\s*(.+)$', l, re.I):
                out.append(line(m[1], m[5], m[4], m[3], m[2], cur))
            elif m := re.match(r'^(.+?)\s+COLOR\s+(.+?)\s+SIZE\s+(.+?)\s+QUANTITY\s+(\d+)', l, re.I):
                p = next((t for t in after[:3] if amount(t)[0] is not None), '')
                out.append(line(m[1], m[3], m[2], m[4], p, cur))
            elif l == 'COLOR' and len(after) >= 7 and after[1] == 'SIZE' and after[3] == 'QUANTITY':
                q = re.match(r'\d+', after[4])
                p = next((t for t in after[5:8] if money_line(t)), '')
                out.append(line(previous_name(lines, i), after[2], after[0], q[0] if q else 1, p, cur))
        elif family == 'nike':
            if (m := re.match(r'^(.+?)(CAD\s*\$?\s*\d[\d,.]*)\s*$', l, re.I)) and after and re.match(r'^Size\s+', after[0], re.I):
                out.append(line(m[1], re.sub(r'^Size\s*:?\s*', '', after[0], flags=re.I), price=m[2], cur=cur))
            elif event in RETURN_EVENTS and re.fullmatch(r'Product details:', l, re.I) and after:
                out.append(line(after[0], field_value(after, 'size'), qty=qty_value(after),
                                cur=cur, sku=field_value(after, 'style')))
            elif (m := re.match(r'^Size:\s*(.+)',l,re.I)) and len(before) >= 2 and before[-2].startswith('Nike '):
                out.append(line(before[-2] + ' – ' + before[-1],m[1],
                                price=after[0] if after and money_line(after[0]) else '',cur=cur))
        elif family == 'adidas' and (m := re.fullmatch(r'Article (?:No\.?|Number):\s*(\S+)', l, re.I)):
            col = next((j for j,t in enumerate(before) if re.match(r'^Colou?r:',t,re.I)),len(before))
            pidx = [j for j,t in enumerate(before[:col]) if money_line(t)]
            size = field_value(before,'size')
            if pidx and size:
                last = pidx[-1]
                first = last-1 if last-1 in pidx else last
                prices = before[first:last+1]
                out.append(line(previous_name(lines, i-len(before)+first, 2), size,
                                field_value(before, 'colou?r'), qty_value(before),min(prices,key=lambda v:amount(v)[0]),cur,m[1]))
        elif family == 'nb' and event in RETURN_EVENTS and l.casefold() == 'standard' and len(after) >= 4:
            if after[2].casefold() in {'apparel','footwear','accessories'} and after[3].casefold() == 'new balance':
                out.append(line(after[0],color=after[1]))
        elif family in {'nb','sportchek','sportinglife'} and re.match(r'^(?:Style #|SKU|Item No\.)\s*:', l, re.I):
            sku = field_value([l]+after, r'style #|sku|item no\.')
            attrs = [l]+after[:next((j for j,t in enumerate(after) if re.fullmatch(r'[-–]{5,}', t) or re.match(r'^(?:item total|order total|subtotal)', t, re.I)),len(after))]
            size = field_value(attrs,'size')
            price = (before[-1] if family == 'sportchek' and before and money_line(before[-1]) else
                     next((t for t in attrs if money_line(t)), ''))
            if size:
                q = qty_value(attrs)
                if q == 1 and family == 'sportchek':
                    q = next((int(t) for t in attrs if t.isdecimal() and len(t)<3), 1)
                out.append(line(previous_name(lines, i), size, field_value(attrs,'(?:primary )?colou?r'), q, price, cur, sku))
        elif family == 'saxx' and l.startswith('Size:') and len(before)>=2 and money_line(before[-1]):
            q = re.match(r'[x×]\s*(\d+)', after[0], re.I) if after else None
            out.append(line(before[-2], l.split(':',1)[1], qty=q[1] if q else 1, price=before[-1], cur=cur))
        elif family == 'goose' and (m := re.fullmatch(r'\w{1,7}\s*\|\s*([^|]+)\s*\|\s*(.+)', l)) and before and after and SIZE.fullmatch(m[2].strip()):
            out.append(line(before[-1], m[2], m[1], qty_value(after[:1]), after[1] if len(after)>1 else '', cur))
        elif family == 'goose' and re.match(r'^Size:', l, re.I) and len(before) >= 2 and re.match(r'^Color:', before[-1], re.I):
            out.append(line(before[-2], l.split(':',1)[1], None, qty_value(after), cur=cur))
        elif family == 'arcteryx' and re.fullmatch(r'\d{5,7}', l) and len(after)>=6:
            n, color, sz, q, p, total = after[:6]
            if q.isdecimal() and SIZE.fullmatch(sz) and re.fullmatch(r'\d+(\.\d{2})?', p):
                out.append(line(n, sz, color, q, p + ' CAD', cur, l))
        elif family in {'mec','hoka','asics','blacktoe','md','patagonia','vuori','decathlon'} and re.match(r'^Size\s*:', l, re.I):
            size = l.split(':',1)[1].strip() or (after[0] if after else None)
            if family == 'decathlon':
                if len(before)>1 and money_line(before[-1]):
                    name = before[-2]
                    if ' / ' in name and re.search(r'[àâçéèêôûëï]', name.split(' / ')[0], re.I):
                        name = name.split(' / ',1)[1]
                    out.append(line(name, size or 'No Size', field_value(after,'colou?r'), qty_value(after[:4]), before[-1], cur))
            elif family == 'hoka' and len(before)>1 and re.match(r'^Quantity:', before[-1], re.I):
                out.append(line(before[-2], size, qty=qty_value(before[-1:]),
                                price=after[0] if after and money_line(after[0]) else '', cur=cur))
            elif family == 'asics' and len(before)>1 and re.match(r'^Quantity:', before[-1], re.I):
                p = next((t for t in after if re.match(r'^Subtotal:', t, re.I)), '')
                out.append(line(before[-2], size, field_value(after,'colou?r'), qty_value(before[-1:]), p,
                                cur, field_value(after, 'sku')))
            elif family == 'blacktoe' and len(before)>1 and re.match(r'^Colou?r:', before[-1], re.I):
                start = next((j for j,t in enumerate(reversed(before)) if re.match(r'^Quantity:', t, re.I)), None)
                if start is not None and start+1 < len(before):
                    n = before[-start-2]
                    if not category_for(n) and start+2 < len(before):
                        n = before[-start-3] + ' ' + n
                    out.append(line(n, size, field_value(before[-1:], 'colou?r'), qty_value(before[-start-1:-start]),
                                    after[0] if after and money_line(after[0]) else '', cur))
            elif family == 'md' and len(before)>=5 and re.match(r'^Colou?r:', before[-1], re.I):
                j = next((j for j in reversed(range(len(before))) if money_line(before[j])), None)
                if j is not None and j:
                    n = before[j-1]
                elif re.fullmatch(r'\d{8,}-\w+',before[-3]):
                    n = before[-4]
                else:
                    n = previous_name(lines,i,5)
                out.append(line(n, size, field_value(before[-1:], 'colou?r'), qty_value(before),
                                before[j] if j is not None else '', cur))
            elif family == 'patagonia' and len(before)>=2 and re.match(r'^Colou?r:', before[-1], re.I):
                if after and re.match(r'^Qty:', after[0], re.I) and len(before) >= 3:
                    match = re.match(r'^Qty:\s*(\d+)', after[0], re.I)
                    out.append(line(before[-3], size, field_value(before[-1:], 'colou?r'),
                                    match[1] if match else 1, after[0], cur))
                elif event in RETURN_EVENTS:
                    out.append(line(before[-2], size, field_value(before[-1:], 'colou?r'), qty_value(after), cur=cur))
            elif family == 'vuori' and len(before)>=3 and re.search(r'\sx\s*\d+$', before[-1]):
                col, q = re.split(r'\s+x\s+', before[-1], maxsplit=1)
                out.append(line(before[-3], size, col, int(q), after[0] if after and money_line(after[0]) else '', cur))
            elif family == 'mec' and len(before)>=2 and re.match(r'^Colou?r:', before[-1], re.I):
                p = after[1] if len(after)>1 and money_line(after[1]) else ''
                out.append(line(before[-2], size, field_value(before[-1:], 'colou?r'), qty_value(after[:1]), p, cur))
        elif family == 'ciele' and l.isdecimal() and len(l)<=2 and after and money_line(after[0]) and before:
            if m := re.fullmatch(r'(.+)\s+-\s+([^\n]+?)\s+-\s+([^\n]+)', before[-1]):
                if SIZE.fullmatch(m[3]):
                    out.append(line(m[1], m[3], m[2], l, after[0], cur))
        elif family == 'shopify' and SIZE.fullmatch(l) and len(before)>=2 and after and money_line(after[0]):
            if re.search(r'^Order Confirmation\b',body,re.M) and category_for(before[-2]):
                out.append(line(before[-2],l,before[-1],price=after[0],cur=cur))
    return dedup_lines(out)


def raw_html(stream, row):
    if not stream or row['mbox_bytes'] > MAX_MESSAGE_BYTES:
        return ''
    stream.seek(row['mbox_offset'])
    data = stream.read(row['mbox_bytes']).partition(b'\n')[2]
    try:
        message = PARSER.parsebytes(data)
        part = message.get_body(('html',))
        if not part or part.get_filename():
            return ''
        parser = VisibleText()
        parser.feed(part.get_content())
        parser.close()
        return sanitize_text(''.join(parser.parts))
    except (UnicodeError, ValueError, LookupError, TypeError, RecursionError):
        return ''


def add_catalog_photos(locations, orders):
    docs = {}
    for order in orders:
        names = {photo_key(row['line'].name) for row in order.items}
        for event in order.events:
            if event.seq in docs:
                continue
            docs[event.seq] = None
            stream, offset, length = locations[event.seq]
            if stream is None or length > MAX_MESSAGE_BYTES:
                continue
            try:
                stream.seek(offset)
                message = PARSER.parsebytes(stream.read(length).partition(b'\n')[2])
                part = message.get_body(('html',))
                if part and not part.get_filename():
                    parser = ReceiptPhotos()
                    parser.feed(part.get_content())
                    parser.close()
                    docs[event.seq] = parser
            except (UnicodeError, ValueError, LookupError, TypeError, RecursionError):
                continue
        for row in order.items:
            item = row['line']
            scores = {}
            for event in order.events:
                doc = docs[event.seq]
                if not doc:
                    continue
                for url, score in doc.candidates(item.name, item.color, item.sku, names).items():
                    # Prefer the source receipt to confirmations with equally strong evidence.
                    score += 5 if event.seq == row['source_seq'] else 0
                    scores[url] = max(score, scores.get(url, 0))
            url = pick_photo(scores)
            row['photo_url'] = url
            row['exclusion'] = exclude_item(item, order.merchant, (url,) if url else scores)


def classify_lines(lines, store, family):
    for item in lines:
        item.brand = item.brand or brand_for(item.name, store)
        item.category = category_for(item.name, item.brand, family)
    return lines


def evidence_for(row, stream):
    subject = sanitize_text(row['subject'])
    body = sanitize_text(row['body'])
    store, family, domain = retailer(row['sender'], body)
    if not store:
        if not row['wardrobe'] or not (party := parseaddr(row['sender'])[0]):
            return [], None
        store, family = party[:80], 'shopify'
    event = event_type(subject, body, family)
    if not event:
        return [], None
    tz = ZoneInfo('Europe/Moscow') if family in {'adidas_ru','lamoda_ru'} else TORONTO
    on = datetime.fromisoformat(row['sent_at']).astimezone(tz).date() if row['sent_at'] else None
    source_kind = 'sqlite_text'
    cur = currency_context(body, store, domain)
    if family == 'amazon':
        results = amazon_sections(subject, body, cur, event)
        if not results:
            return [], ('order_reference_missing' if row['wardrobe'] else None)
    else:
        number = order_number(subject, body, family)
        if not number and family == 'lulu':
            refs = {m.upper() for m in re.findall(r'\b[cC][a-zA-Z]{0,2}\d{10,20}\b',row['body'])}
            if len(refs) == 1:
                number = refs.pop()
        items = retailer_lines(body, family, cur, event)
        original_body = body
        partial_refund = family in {'shopify','blacktoe'} and event == 'refunded' and bool(
            re.search(r'\bSome items in your order have been refunded\b',body,re.I))
        has_refunded_items = bool(re.search(r'(?m)^\s*Refunded\s*$',body,re.I))
        if (family != 'shopify' or bool(store in HOUSE_BRANDS)) and (
                not body.strip() or (not any(category_for(i.name, i.brand, family) for i in items)
                 and (family in REVIEWABLE_FAMILIES or row['wardrobe'])) or (partial_refund and not has_refunded_items)):
            if alt := raw_html(stream, row):
                cur = cur or currency_context(alt, store, domain)
                alternate_items = retailer_lines(alt, family, cur, event)
                if alternate_items or not items:
                    items = alternate_items
                    source_kind = 'mbox_html' if alternate_items or not body.strip() else source_kind
                number = number or order_number(subject, alt, family)
                body = original_body + '\n' + alt
        results = [(number, items, parse_date(body, family, on))]

    order_only = family in {'shopify','blacktoe'} and event == 'refunded' and bool(
        re.search(r'\bSome items in your order have been refunded\b',body,re.I)) and not bool(
        re.search(r'(?m)^\s*Refunded\s*$',body,re.I))
    ref = RETURN_REFERENCE.search(subject + '\n' + body) if event in RETURN_EVENTS else None
    md_ref = re.search(r'(?i)(?:return|receipt) no\.\s*(\d{7,})',subject + '\n' + body) if family == 'md' and event in RETURN_EVENTS else None
    whole = bool(event == 'refunded' and re.search(r'(?im)^\s*Your (?:entire )?order has been refunded[.!]?\s*$', body))
    return [Evidence(row['seq'], row['id'], store, n, event, row['sent_at'], on, explicit,
                     classify_lines(items, store, family), 'order_number' if n else 'unlinked',
                     whole, namekey(ref[0]) if ref else namekey(md_ref[1]) if md_ref else None, source_kind, order_only)
            for n, items, explicit in results], None


def product_key(value):
    value = re.sub(r'\s*\*\s*(regular|reg)\b', '', value, flags=re.I)
    value = re.sub(r'(?i)\bhigh[ -]rise\b', 'HR', value)
    return namekey(value)


def size_key(value):
    if value and re.fullmatch(r'(?:[mw]\s*)?\d+(?:\.\d+)?\s*/\s*(?:[mw]\s*)?\d+(?:\.\d+)?',value,re.I):
        return tuple(re.findall(r'\d+(?:\.\d+)?',value))
    return namekey(value)


def variant_matches(left, right):
    if left.audience and right.audience and left.audience != right.audience:
        return False
    for attr in ('size','color'):
        key = size_key if attr == 'size' else namekey
        a, b = key(getattr(left,attr)), key(getattr(right,attr))
        if a and b and a != b:
            if attr == 'size' or not (a.startswith(b) or b.startswith(a)):
                return False
    return True


def match_line(probe, options, allow_prefix=False, strict_sku=False):
    available = [(i, item['line']) for i,item in enumerate(options) if variant_matches(probe,item['line'])
                 and not (strict_sku and probe.sku and item['line'].sku and namekey(probe.sku) != namekey(item['line'].sku))]
    if probe.sku:
        matches = [i for i,item in enumerate(options) if namekey(probe.sku) == namekey(item['line'].sku)
                   and (not probe.size or not item['line'].size or size_key(probe.size) == size_key(item['line'].size))]
        if matches:
            return matches[0] if len(matches) == 1 else None
    key = product_key(probe.name)
    exact = [i for i,l in available if product_key(l.name) == key]
    if exact:
        return exact[0] if len(exact) == 1 else None
    if allow_prefix:
        indices = []
        for i,l in available:
            other = product_key(l.name)
            short, long = sorted([key,other], key=len)
            if len(short) >= 18 and (long.startswith(short) or long.endswith(short)) and (len(short) >= .72 * len(long)
                                                         or re.search(r'\.\.\.|…', probe.name)):
                indices.append(i)
        if len(indices) == 1:
            return indices[0]
        def words(s):
            s = re.sub(r'(?i)\b(?:HR|high[ -]rise)\b','highrise',s)
            return set(re.findall(r'[a-z]{3,}',s.lower())) - {'the','men','mens','women','womens','unisex','regular','reg'}
        common = words(probe.name)
        candidates = []
        for i,item in available:
            other = words(item.name)
            overlap = len(common & other)
            if overlap >= 3 and overlap >= .68 * min(len(common),len(other)) and (
                    (probe.size and item.size and size_key(probe.size)==size_key(item.size)) or
                    (probe.color and item.color and namekey(probe.color)==namekey(item.color))):
                candidates.append(i)
        if len(candidates) == 1:
            return candidates[0]
    return None


def order_key(merchant, number):
    return hashlib.sha256(f'{merchant.casefold()}\n{number}'.encode()).hexdigest()[:24]


def fill_purchase(order):
    positives = sorted((ev for ev in order.events if ev.event not in RETURN_EVENTS),
                       key=lambda ev: (-PAYMENT_PRIORITY.get(ev.event,0), ev.sent_at or '9999', ev.seq))
    date_priority = {'confirmation':0,'receipt':1,'shipment':2,'delivered':3,'update':4}
    dated = sorted((ev for ev in order.events if ev.explicit_date),
                   key=lambda ev: (date_priority.get(ev.event,5), ev.sent_at or '9999', ev.seq))
    order.first_seen = min((ev.on for ev in positives if ev.on), default=None)
    if dated:
        order.purchased_on, order.purchase_basis, order.purchase_seq = dated[0].explicit_date, 'stated_order_date', dated[0].seq
    else:
        confirmed = min((ev for ev in positives if ev.event in ORDER_EVENTS and ev.on),
                        key=lambda ev: (ev.on, ev.seq), default=None)
        if confirmed:
            order.purchased_on, order.purchase_basis, order.purchase_seq = confirmed.on, confirmed.event + '_email_date', confirmed.seq
    whole = [len(ev.lines) for ev in positives if ev.event == 'confirmation' and ev.lines]
    order.confirmed_full_item_count = max(whole, default=None)
    has_confirmed_attire = any(ev.event == 'confirmation' and any(item.category for item in ev.lines) for ev in positives)

    for ev in positives:
        for item in ev.lines:
            idx = match_line(item, order.items, allow_prefix=True, strict_sku=ev.merchant=='Lamoda')
            if idx is None:
                if ev.event not in ORDER_EVENTS and has_confirmed_attire:
                    # Fulfillment templates frequently rename the confirmed product. Leave such variant lines out
                    # of inventory; the email is still retained on the order.
                    continue
                order.items.append({'line': item, 'sources': {ev.seq: ev.event}, 'source_seq': ev.seq,
                                    'price_seq': ev.seq if item.price_minor is not None else None,
                                    'status': 'no_return_evidence', 'return_seq': None, 'return_qty': None})
                continue
            row = order.items[idx]
            row['sources'][ev.seq] = ev.event
            prior = row['line']
            for attr in ('size','color','sku','brand','category','audience'):
                if not getattr(prior,attr) and getattr(item,attr):
                    setattr(prior,attr,getattr(item,attr))
            prior.quantity = max(prior.quantity,item.quantity)
            if prior.price_minor is None and item.price_minor is not None:
                prior.price_minor, prior.currency = item.price_minor, item.currency
                row['price_seq'] = ev.seq
            elif prior.price_minor == item.price_minor and prior.currency is None:
                prior.currency = item.currency


def link_by_product(ev, orders):
    if not ev.on or not ev.lines:
        return []
    linked = defaultdict(list)
    for probe in ev.lines:
        if not probe.category:
            continue
        matches = []
        for order in orders:
            if order.merchant != ev.merchant:
                continue
            bought = order.purchased_on or order.first_seen
            if (not bought or (ev.explicit_date and ev.explicit_date != bought)
                    or not 0 <= (ev.on-bought).days <= (120 if ev.event in RETURN_EVENTS else 35)):
                continue
            idx = match_line(probe,order.items)
            if idx is not None:
                item = order.items[idx]['line']
                if (probe.sku and namekey(probe.sku) == namekey(item.sku)) or (probe.size and item.size and
                    (probe.color and item.color or ev.explicit_date)):
                    matches.append((order.id,idx))
        if len(matches) == 1:
            linked[matches[0][0]].append(probe)
    return list(linked.items())


def dedup_events(events):
    seen = set()
    for ev in events:
        if ev.seq not in seen:
            seen.add(ev.seq)
            yield ev


def apply_returns(order):
    for ev in sorted((e for e in dedup_events(order.events) if e.event in RETURN_EVENTS),
                     key=lambda e:(e.sent_at or '',e.seq)):
        if ev.order_only:
            order.unassigned_returns += 1
            continue
        linked = {}
        for probe in ev.lines:
            i = match_line(probe, order.items, allow_prefix=True)
            if i is not None:
                linked[i] = probe.quantity
        if ev.whole_order and order.confirmed_full_item_count and len(order.items) == order.confirmed_full_item_count:
            linked.update({i: item['line'].quantity for i,item in enumerate(order.items)})
        # An original order number plus a named physical return documents the historical purchase.
        if not order.items and ev.number and ev.event in {'returned','refunded'}:
            for probe in ev.lines:
                if not probe.category or re.search(r'\.\.\.|…', probe.name):
                    continue
                probe.price_minor = None
                order.items.append({'line': probe, 'sources': {}, 'source_seq': ev.seq, 'price_seq': None,
                                    'status': 'no_return_evidence', 'return_seq': None, 'return_qty': None,
                                    'return_only': True})
                linked[len(order.items)-1] = probe.quantity
        relevant_lines = [probe for probe in ev.lines if probe.category]
        if not ev.lines or any(match_line(probe,order.items,allow_prefix=True) not in linked for probe in relevant_lines):
            if not ev.whole_order or not linked:
                order.unassigned_returns += 1
        if not linked:
            continue
        for i,qty in linked.items():
            row = order.items[i]
            row['sources'][ev.seq] = ev.event
            if RETURN_RANK[ev.event] >= RETURN_RANK[row['status']]:
                row['status'], row['return_seq'] = ev.event, ev.seq
                row['return_qty'] = min(qty, row['line'].quantity)


def extract(input_rows):
    orders, unlinked, issues = {}, [], []
    stats = Counter()
    for row, stream in input_rows:
        stats['source_messages'] += 1
        if not row['subject'] and not row['body']:
            continue
        events, issue = evidence_for(row, stream)
        if issue:
            issues.append((row['seq'], row['id'], 'Amazon', row['sent_at'], issue))
        for ev in events:
            if ev.number or (ev.event in ORDER_EVENTS and any(l.category for l in ev.lines)):
                key = order_key(ev.merchant, ev.number or f'email:{ev.seq}')
                if not ev.number:
                    ev.link_method = 'confirmation_without_order_number'
                orders.setdefault(key, Order(key,ev.merchant,ev.number)).events.append(ev)
            elif ev.event in RETURN_EVENTS or any(l.category for l in ev.lines):
                unlinked.append(ev)

    # A return ID can link a completion/refund email to a request with the original order number.
    references = defaultdict(set)
    for order in orders.values():
        for ev in order.events:
            if ev.reference:
                references[order.merchant,ev.reference].add(order.id)
        fill_purchase(order)
    for ev in unlinked:
        ref_matches = references.get((ev.merchant,ev.reference),set())
        if len(ref_matches) == 1:
            ev.link_method = 'return_reference'
            orders[next(iter(ref_matches))].events.append(ev)
            continue
        matches = link_by_product(ev, orders.values())
        if matches:
            for key, subset in matches:
                orders[key].events.append(Evidence(ev.seq,ev.message_id,ev.merchant,None,ev.event,ev.sent_at,
                    ev.on,ev.explicit_date,subset,'unique_product_variant',False,ev.reference,ev.body_source,ev.order_only))
                if ev.reference:
                    references[ev.merchant,ev.reference].add(key)
            matched = {id(l) for _, subset in matches for l in subset}
            if all(not l.category or id(l) in matched for l in ev.lines):
                continue
        issues.append((ev.seq,ev.message_id,ev.merchant,ev.sent_at,
                       'return_not_attributed' if ev.event in RETURN_EVENTS else 'order_reference_missing'))
    selected = []
    for order in orders.values():
        apply_returns(order)
        if any(item['line'].category for item in order.items):
            selected.append(order)
            for ev in order.events:
                if ev.event in RETURN_EVENTS and order.unassigned_returns and not any(
                        ev.seq in item['sources'] for item in order.items):
                    if not ev.lines or any(item.category for item in ev.lines):
                        issues.append((ev.seq,ev.message_id,ev.merchant,ev.sent_at,'return_item_uncertain'))
        else:
            for ev in order.events:
                if ev.event in ORDER_EVENTS and not ev.lines and ev.merchant in HOUSE_BRANDS:
                    issues.append((ev.seq,ev.message_id,ev.merchant,ev.sent_at,'item_details_not_extracted'))
    stats['orders'] = len(selected)
    return selected, list(dict.fromkeys(issues)), stats


SCHEMA = '''
CREATE TABLE IF NOT EXISTS wardrobe_history_import (
  source_database TEXT PRIMARY KEY,
  source_messages INTEGER NOT NULL,
  imported_at TEXT NOT NULL DEFAULT (datetime('now')),
  rules_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wardrobe_history_sources (
  source_seq INTEGER PRIMARY KEY,
  source_database TEXT NOT NULL REFERENCES wardrobe_history_import(source_database),
  archive_seq INTEGER NOT NULL,
  archive_id TEXT NOT NULL,
  UNIQUE (source_database, archive_seq)
);
CREATE TABLE IF NOT EXISTS wardrobe_orders (
  order_id TEXT PRIMARY KEY, merchant TEXT NOT NULL, order_number TEXT,
  purchased_on TEXT, purchase_date_basis TEXT, purchase_date_source_seq INTEGER,
  first_purchase_email_on TEXT, unassigned_return_emails INTEGER NOT NULL,
  UNIQUE (merchant, order_number),
  CHECK ((purchased_on IS NULL) = (purchase_date_basis IS NULL))
);
CREATE TABLE IF NOT EXISTS wardrobe_order_emails (
  order_id TEXT NOT NULL REFERENCES wardrobe_orders(order_id),
  source_seq INTEGER NOT NULL, archive_id TEXT NOT NULL, event_type TEXT NOT NULL,
  sent_at TEXT, link_method TEXT NOT NULL, body_source TEXT NOT NULL,
  PRIMARY KEY (order_id, source_seq)
);
CREATE TABLE IF NOT EXISTS wardrobe_purchase_items (
  item_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES wardrobe_orders(order_id),
  name TEXT NOT NULL, brand TEXT, category TEXT NOT NULL, size TEXT, color TEXT, sku TEXT,
  quantity INTEGER NOT NULL CHECK (quantity > 0), price_minor INTEGER CHECK (price_minor >= 0),
  currency TEXT, price_basis TEXT CHECK (price_basis IN ('listed_item_price','reported_line_price')),
  currency_assumed INTEGER NOT NULL DEFAULT 0 CHECK (currency_assumed IN (0,1)),
  evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('purchase_email','return_only')),
  return_status TEXT NOT NULL CHECK (return_status IN ('no_return_evidence','return_requested','returned','refunded','cancelled')),
  affected_quantity INTEGER CHECK (affected_quantity BETWEEN 1 AND quantity),
  source_seq INTEGER NOT NULL, price_source_seq INTEGER, return_source_seq INTEGER,
  photo_url TEXT,
  FOREIGN KEY (order_id, source_seq) REFERENCES wardrobe_order_emails(order_id, source_seq),
  FOREIGN KEY (order_id, price_source_seq) REFERENCES wardrobe_order_emails(order_id, source_seq),
  FOREIGN KEY (order_id, return_source_seq) REFERENCES wardrobe_order_emails(order_id, source_seq),
  CHECK ((price_minor IS NULL) = (price_source_seq IS NULL))
);
CREATE TABLE IF NOT EXISTS wardrobe_item_emails (
  item_id TEXT NOT NULL REFERENCES wardrobe_purchase_items(item_id),
  order_id TEXT NOT NULL, source_seq INTEGER NOT NULL,
  PRIMARY KEY (item_id, source_seq),
  FOREIGN KEY (order_id, source_seq) REFERENCES wardrobe_order_emails(order_id, source_seq)
);
CREATE TABLE IF NOT EXISTS wardrobe_history_review (
  source_seq INTEGER NOT NULL, archive_id TEXT NOT NULL,
  merchant TEXT NOT NULL, sent_at TEXT, reason TEXT NOT NULL,
  PRIMARY KEY (source_seq, reason)
);
CREATE INDEX IF NOT EXISTS wardrobe_purchase_items_order ON wardrobe_purchase_items(order_id);
CREATE INDEX IF NOT EXISTS wardrobe_order_emails_seq ON wardrobe_order_emails(source_seq);
CREATE INDEX IF NOT EXISTS wardrobe_orders_date ON wardrobe_orders(purchased_on);
DROP VIEW IF EXISTS wardrobe_purchase_timeline;
CREATE VIEW wardrobe_purchase_timeline AS
SELECT p.item_id, o.order_id, o.order_number, o.merchant, p.name, p.brand, p.category, p.size,
       p.color, p.sku, p.quantity, p.price_minor, p.price_minor / 100.0 AS reported_price,
       p.currency, p.price_basis, o.purchased_on, o.purchase_date_basis, o.purchase_date_source_seq,
       coalesce(o.purchased_on,o.first_purchase_email_on) AS timeline_date,
       CASE WHEN o.purchased_on IS NOT NULL THEN o.purchase_date_basis
            WHEN o.first_purchase_email_on IS NOT NULL THEN 'first_fulfillment_email' END AS timeline_date_basis,
       p.return_status, p.affected_quantity, o.unassigned_return_emails,
       (SELECT oe.event_type FROM wardrobe_order_emails oe WHERE oe.order_id=o.order_id
        AND oe.event_type IN ('return_requested','returned','refunded','cancelled')
        ORDER BY CASE oe.event_type WHEN 'refunded' THEN 3 WHEN 'returned' THEN 2 WHEN 'cancelled' THEN 2 ELSE 1 END DESC,
        oe.sent_at DESC LIMIT 1) AS order_return_status,
       p.evidence_kind,
       p.source_seq, p.price_source_seq, p.return_source_seq, e.archive_id, p.photo_url,
       s.source_database, s.archive_seq
FROM wardrobe_purchase_items p JOIN wardrobe_orders o USING (order_id)
JOIN wardrobe_order_emails e ON e.order_id = p.order_id AND e.source_seq = p.source_seq
LEFT JOIN wardrobe_history_sources s ON s.source_seq = p.source_seq;
'''


def saved_item_ids(db):
    prior = defaultdict(dict)
    if db.execute("SELECT 1 FROM sqlite_schema WHERE name='wardrobe_purchase_items'").fetchone():
        for order_id, item_id, name, size, color, sku in db.execute(
                'SELECT order_id,item_id,name,size,color,sku FROM wardrobe_purchase_items'):
            prior[order_id][item_id] = Line(name, size=size, color=color, sku=sku)
    return prior


def assign_item_ids(order_id, selected, prior):
    proposed = [order_key(order_id, f'{i}:{product_key(row["line"].name)}|{row["line"].size}|'
                         f'{row["line"].color}|{row["line"].sku}') for i, row in selected]
    unused = set(prior) - set(proposed)
    by_line = {}
    for n, (_, row) in enumerate(selected):
        if proposed[n] in prior:
            continue
        item = row['line']
        by_line[n] = {old_id for old_id in unused if (
            (candidate := prior[old_id]) and variant_matches(item, candidate)
            and ((item.sku and candidate.sku and namekey(item.sku) == namekey(candidate.sku))
                 or (not (item.sku and candidate.sku) and product_key(item.name) == product_key(candidate.name))))}
    claims = Counter(old_id for ids in by_line.values() for old_id in ids)
    for n, ids in by_line.items():
        if len(ids) == 1 and claims[old_id := next(iter(ids))] == 1:
            proposed[n] = old_id
    return proposed


def ensure_currency_assumed_column(db):
    if not any(col[1] == 'currency_assumed' for col in db.execute('PRAGMA table_info(wardrobe_purchase_items)')):
        db.execute('''ALTER TABLE wardrobe_purchase_items ADD COLUMN currency_assumed INTEGER
                      NOT NULL DEFAULT 0 CHECK (currency_assumed IN (0,1))''')


def load(db, sources, provenance, orders, issues, stats, audience="all", default_currency="CAD"):
    db.execute('PRAGMA foreign_keys=ON')
    before = db.execute('SELECT COUNT(*) FROM sqlite_schema WHERE name = ?', ('items',)).fetchone()[0]
    count = db.execute('SELECT COUNT(*) FROM items').fetchone()[0] if before else None
    db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA)
    try:
        if not any(col[1] == 'photo_url' for col in db.execute('PRAGMA table_info(wardrobe_purchase_items)')):
            db.execute('ALTER TABLE wardrobe_purchase_items ADD COLUMN photo_url TEXT')
        ensure_currency_assumed_column(db)
        prior = saved_item_ids(db)
        for table in ('wardrobe_item_emails','wardrobe_purchase_items','wardrobe_order_emails',
                      'wardrobe_orders','wardrobe_history_review','wardrobe_history_sources','wardrobe_history_import'):
            db.execute(f'DELETE FROM {table}')
        db.executemany('INSERT INTO wardrobe_history_import(source_database,source_messages,rules_version) VALUES (?,?,?)',
                       ((str(source), count, 'wardrobe-email-v3') for source, count in sources.items()))
        db.executemany('INSERT INTO wardrobe_history_sources VALUES (?,?,?,?)', provenance)
        excluded = Counter()
        kept_orders = 0
        for order in orders:
            selected = []
            for i, row in enumerate(order.items):
                if not row['line'].category:
                    continue
                if (reason := row.get('exclusion', exclude_item(row['line'], order.merchant))) and (reason not in {'women', 'kids'} or audience == 'men'):
                    excluded[reason] += 1
                else:
                    selected.append((i, row))
            if not selected:
                continue
            kept_orders += 1
            db.execute('INSERT INTO wardrobe_orders VALUES (?,?,?,?,?,?,?,?)',
                       (order.id,order.merchant,order.number,serial_date(order.purchased_on),order.purchase_basis,
                        order.purchase_seq,serial_date(order.first_seen),order.unassigned_returns))
            db.executemany('INSERT INTO wardrobe_order_emails VALUES (?,?,?,?,?,?,?)',
                           ((order.id, e.seq,e.message_id,e.event,e.sent_at,e.link_method,e.body_source)
                             for e in dedup_events(order.events)))
            ids = assign_item_ids(order.id, selected, prior[order.id])
            for iid, (_, row) in zip(ids, selected, strict=True):
                item = row['line']
                recorded_currency = (item.currency or '').strip()
                db.execute('''INSERT INTO wardrobe_purchase_items
                    (item_id,order_id,name,brand,category,size,color,sku,quantity,price_minor,currency,price_basis,
                     evidence_kind,return_status,affected_quantity,source_seq,price_source_seq,return_source_seq,photo_url,currency_assumed)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                           (iid,order.id,item.name,item.brand,item.category,item.size,item.color,item.sku,item.quantity,
                            item.price_minor,recorded_currency or default_currency,
                            ('listed_item_price' if item.quantity == 1 else 'reported_line_price') if item.price_minor is not None else None,
                            'return_only' if row.get('return_only') else 'purchase_email',row['status'],row['return_qty'],
                            row['source_seq'],row['price_seq'],row['return_seq'],row.get('photo_url'),int(not recorded_currency)))
                db.executemany('INSERT INTO wardrobe_item_emails VALUES (?,?,?)',
                               ((iid,order.id,seq) for seq in row['sources']))
        db.executemany('INSERT OR IGNORE INTO wardrobe_history_review VALUES (?,?,?,?,?)',issues)
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Wardrobe history contains missing evidence references.')
        if db.execute('''SELECT count(*) FROM wardrobe_order_emails e LEFT JOIN wardrobe_history_sources s
                         ON e.source_seq=s.source_seq WHERE s.archive_id IS NULL OR s.archive_id<>e.archive_id''').fetchone()[0]:
            raise ValueError('Wardrobe email evidence could not be traced to its original mailbox.')
        if db.execute("SELECT 1 FROM sqlite_schema WHERE name='wardrobe_purchase_favourites'").fetchone():
            orphaned = set(db.execute('''SELECT item_id FROM wardrobe_purchase_favourites
                        WHERE item_id NOT IN (SELECT item_id FROM wardrobe_purchase_items)'''))
            if any(iid in order for (iid,) in orphaned for order in prior.values()):
                raise ValueError('The rebuild would remove a saved favourite; verify the changed item identity.')
        if count is not None and db.execute('SELECT COUNT(*) FROM items').fetchone()[0] != count:
            raise ValueError('Existing wardrobe inventory count unexpectedly changed.')
        stats['existing_inventory_rows'] = count
        stats['orders'] = kept_orders
        stats['excluded'] = dict(excluded)
        metrics = db.execute('''SELECT COUNT(*) AS items, sum(brand IS NOT NULL) AS known_brands,
            sum(size IS NOT NULL) AS known_sizes, sum(price_minor IS NOT NULL) AS known_prices,
            sum(purchased_on IS NOT NULL) AS known_purchase_dates, sum(timeline_date IS NOT NULL) AS timeline_rows,
            sum(evidence_kind='return_only') AS return_only, sum(photo_url IS NOT NULL) AS catalog_photos
            FROM wardrobe_purchase_timeline''')
        stats.update(dict(zip((col[0] for col in metrics.description),metrics.fetchone())))
        stats['linked_email_rows'] = db.execute('SELECT COUNT(*) FROM wardrobe_order_emails').fetchone()[0]
        stats['review_rows'] = db.execute('SELECT COUNT(*) FROM wardrobe_history_review').fetchone()[0]
        return_counts = dict(db.execute('SELECT return_status, count(*) FROM wardrobe_purchase_items GROUP BY 1'))
        if stats['items'] == 0:
            raise ValueError('No wardrobe purchases found; check the archive and parser.')
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {**stats,'return_status': return_counts}


def serial_date(value):
    return value.isoformat() if value else None


SOURCE_STRIDE = 10**9


def existing_sources(target):
    if not target.is_file():
        return []
    with closing(sqlite3.connect(target.as_uri()+'?mode=ro', uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_schema WHERE name='wardrobe_history_import'").fetchone():
            return []
        return [Path(path) for path, in db.execute('SELECT source_database FROM wardrobe_history_import ORDER BY rowid')]


def shopping_rows(inputs, provenance, locations, duplicates):
    fingerprints, identities = set(), set()
    for i, (source, db, stream) in enumerate(inputs):
        for original_row in db.execute('SELECT * FROM messages ORDER BY seq'):
            row = dict(original_row)
            local = row['seq']
            if not 0 < local < SOURCE_STRIDE:
                raise ValueError('Shopping message sequence out of range.')
            fingerprint = re.sub(r'-\d+$', '', row['id'])
            # Gmail exports from different accounts can add different headers to the same message.
            # A reused Message-ID alone is insufficient; also require the same sender, date and body.
            identity = None
            if row['message_id'] and row['sent_at']:
                identity = (
                    row['message_id'].strip().casefold(), row['sent_at'],
                    parseaddr(row['sender'])[1].casefold(), row['subject'],
                    hashlib.sha256(row['body'].encode()).digest(),
                )
            if i and (fingerprint in fingerprints or (identity and identity in identities)):
                duplicates[source] += 1
                continue
            fingerprints.add(fingerprint)
            if identity:
                identities.add(identity)
            seq = i * SOURCE_STRIDE + local
            row['seq'] = seq
            provenance.append((seq, str(source), local, row['id']))
            locations[seq] = (stream, row['mbox_offset'], row['mbox_bytes'])
            yield row, stream


def run(sources, target, audience="all", default_currency="CAD"):
    previous = existing_sources(target)
    sources = sources or previous
    if not sources:
        raise ValueError('Pass --source /path/to/mail.sqlite3 on the first import.')
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if target in sources or len(set(sources)) != len(sources):
        raise ValueError('Use distinct shopping source databases and a separate destination.')
    if previous != sources[:len(previous)]:
        raise ValueError('Keep existing shopping sources in their original order and append new ones.')
    counts, missing_mbox, provenance, locations, duplicates = {}, [], [], {}, Counter()
    with ExitStack() as stack:
        inputs = []
        seen_mailboxes = set()
        for source in sources:
            db = stack.enter_context(closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)))
            db.row_factory = sqlite3.Row
            path, size, mtime = db.execute('SELECT path,bytes,mtime_ns FROM import_source').fetchone()
            original = Path(path).resolve()
            if (original,size,mtime) in seen_mailboxes:
                raise ValueError('Two shopping databases index the same mailbox.')
            seen_mailboxes.add((original,size,mtime))
            if original.is_file() and (original.stat().st_size,original.stat().st_mtime_ns) == (size,mtime):
                stream = stack.enter_context(original.open('rb'))
            else:
                stream = None
                missing_mbox.append(str(source))
            counts[source] = db.execute('SELECT count(*) FROM messages').fetchone()[0]
            inputs.append((source, db, stream))
        orders, issues, stats = extract(shopping_rows(inputs, provenance, locations, duplicates))
        add_catalog_photos(locations, orders)
    stats['duplicate_shopping_messages'] = sum(duplicates.values())
    stats['source_databases'] = len(sources)
    if missing_mbox:
        stats['original_mbox_unavailable'] = missing_mbox
    with closing(sqlite3.connect(target,timeout=15)) as db:
        print(json.dumps(load(db,counts,provenance,orders,issues,stats,audience,default_currency),indent=2))


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--source',type=Path,action='append',help='Shopping SQLite database; repeat for multiple mailboxes. Defaults to the saved sources.')
    cli.add_argument('--target',type=Path,default=Path(__file__).resolve().parent.parent/'data/jop.db')
    cli.add_argument('--audience', choices=('all', 'men'), default='all', help='Keep all apparel (default), or exclude explicitly women/kids items.')
    cli.add_argument('--default-currency', default='CAD', help='ISO currency to assume only when a receipt omits currency (default: CAD).')
    args = cli.parse_args()
    if not re.fullmatch(r'[A-Z]{3}', args.default_currency):
        cli.error('--default-currency must be a three-letter uppercase currency code')
    os.umask(0o077)
    run([source.expanduser().resolve() for source in args.source or []],args.target.expanduser().resolve(), args.audience, args.default_currency)


if __name__ == '__main__':
    main()
