"""Find catalog photos and audience evidence in the original receipt HTML."""

import re
from collections import defaultdict
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

# Deliberately limited to shared catalog assets. Never use tracking pixels,
# email links, customer images, or receipt URLs with user-specific parameters.
CATALOG = {
    'contents.mediadecathlon.com': r'/p\d+/[^/]+/picture\.(?:jpg|png)',
    'm.media-amazon.com': r'/images/I/[\w+.-]+\.(?:jpg|png)',
    'images-na.ssl-images-amazon.com': r'/images/I/[\w+.-]+\.(?:jpg|png)',
    'calvinklein.scene7.com': r'/is/image/CalvinKlein/[\w-]+',
    'images.lululemon.com': r'/is/image/lululemon/L[MWU][\w-]+_\d+.*',
    'cdn.shopify.com': r'/s/files/(?:\d+/)+(?:files|products)/[^/?]+\.(?:jpe?g|png|webp|gif)',
    'static.massimodutti.net': r'/assets/public/(?:[\w-]+/)+[\w.-]+\.jpg',
    'assets.adidas.com': r'/images/[^?]+\.(?:jpg|png)',
    'demandware.edgesuite.net': r'/aagl_prd/on/demandware\.static/Sites-adidas-RU-Site/Sites-adidas-products/ru_RU/v\d+/medium/[\w.-]+\.(?:jpg|png)',
    'pi.lmcdn.ru': r'/img\d+x\d+/[A-Z]/[A-Z]/[A-Z0-9]+_\d+_\d+_v\d+\.(?:jpg|png)',
    'ca.shop.runningroom.com': r'/media/catalog/product/[a-z0-9]/[a-z0-9]/[\w.-]+\.(?:jpg|png)',
    'cdn11.bigcommerce.com': r'/s-[\w]+/product_images/[^?]+\.(?:jpg|png)',
    'assets.thenorthface.com': r'/images/[^?]+\.(?:jpg|png)',
    'images.thenorthface.com': r'/is/image/TheNorthFace/{1,2}NF[\w-]+_hero',
    'images.ctfassets.net': r'/hnk2vsx53n6l/[A-Za-z0-9]+/[a-f0-9]{32}/[a-f0-9]{40}\.(?:jpg|png)',
    'upload.on-running.com': r'/spree/products/\d+/product/[a-f0-9]{40}\.(?:jpg|png)',
    'library.on.com': r'/thumbnails/v1/[A-Z0-9]{8,20}',
    'images.canadagoose.com': r'/image/upload/[^?]+/product-image/[\w-]+\.jpg',
    'media-www.atmosphere.ca': r'/product/[^?]+',
    'media-www.sportchek.ca': r'/product/[^?]+',
    'dms.deckers.com': r'/hoka/image/upload/[^?]+\.(?:jpg|png)',
    'nb.scene7.com': r'/is/image/NB/[\w-]+',
    'images.asics.com': r'/is/image/asics/[\w-]+',
    'secure-images.nike.com': r'/is/image/DotCom/[\w-]+',
    'static.nike.com': r'/a/images/[^?]+\.(?:jpg|png)',
    'www.patagonia.com': r'/dis/dw/image/[^?]+/images/hi-res/[\w-]+\.jpg',
    'cieleathletics.com': r'/wp-content/uploads/[^?]+\.(?:jpg|png)',
    'image.uniqlo.com': r'/[^?]+/goods/[^?]+',
    'images.arcteryx.com': r'/[^?]+\.(?:jpg|png|webp)',
}
SCENE7 = {'calvinklein.scene7.com', 'images.lululemon.com', 'nb.scene7.com', 'images.asics.com', 'secure-images.nike.com', 'images.thenorthface.com'}


def catalog_url(src):
    try:
        res = urlsplit(src.strip())
    except ValueError:
        return None
    if res.scheme not in ('http', 'https', '') or res.netloc not in CATALOG or not re.fullmatch(CATALOG[res.netloc], res.path, re.I):
        return None
    path = res.path
    if res.netloc == 'm.media-amazon.com':
        path = re.sub(r'\._[A-Z0-9]+_\.(jpg|png)$', r'._SL480_.\1', path)
    elif res.netloc == 'cdn.shopify.com':
        path = re.sub(r'_compact_cropped(?=\.)', '_480x', path)
    elif res.netloc == 'assets.adidas.com':
        path = re.sub(r'(?<=/)w_\d+,', 'w_480,', path)
    elif res.netloc == 'images.thenorthface.com':
        path = path.replace('TheNorthFace//', 'TheNorthFace/')
    return urlunsplit(('https', res.netloc, path, 'wid=384&hei=384' if res.netloc in SCENE7 else '', ''))


def key(value):
    # Trademark symbols and curly apostrophes should not create separate words.
    return ' '.join(re.findall(r'[^\W_]+', re.sub(r"[™®'’]", '', value.casefold())))


class ReceiptPhotos(HTMLParser):
    VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    BLOCKS = {'article','div','li','section','table','td','tr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.images = []

    def handle_starttag(self, tag, pairs):
        if tag == 'img':
            attr = dict(pairs)
            if url := catalog_url(attr.get('src') or ''):
                self.images.append((url, key(attr.get('alt') or ''), tuple(node for node in self.stack if node[0] in self.BLOCKS)))
            return
        if tag not in self.VOID:
            self.stack.append((tag, []))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, value):
        if value.strip() and not any(t in {'style','script','head'} for t, _ in self.stack):
            for tag, contents in self.stack:
                if tag in self.BLOCKS:
                    contents.append(value)

    def candidates(self, name, color, sku, other_names):
        # A dedicated product row is stronger evidence than a large email section.
        n = key(name)
        c = key(color or '')
        scores = {}
        for url, alt, blocks in self.images:
            parsed = urlsplit(url)
            path_key = key(parsed.path)
            sku_hit = sku and f' {key(sku)} ' in f' {path_key} '
            if parsed.hostname == 'pi.lmcdn.ru' and sku and len(sku) >= 12:
                # Lamoda labels inventory with a variant suffix after the 12-character catalog code.
                sku_hit = parsed.path.rsplit('/', 1)[-1].split('_', 1)[0].casefold() == sku[:12].casefold()
            score = 100 if sku_hit else 0
            if n == alt or alt.startswith(n + ' '):
                score = max(score, 80)
            # Merchants may truncate very long product names in ALT attributes.
            elif len(alt) >= 45 and n.startswith(alt + ' '):
                score = max(score, 75)
            for _, text in reversed(blocks):
                context = key(' '.join(text))
                if len(context) > 720 or n not in context:
                    continue
                if any(other != n and other in context for other in other_names):
                    continue
                score = max(score, 65 if len(context) <= max(230, len(n) + 110) else 48)
                if c and f' {c} ' in f' {context} ':
                    score += 10
                break
            if score >= 65:
                scores[url] = max(score, scores.get(url, 0))
        return scores


def pick_photo(scores):
    if not scores:
        return None
    ordered = sorted(scores, key=lambda url: (-scores[url],url))
    if len(ordered) == 1 or scores[ordered[0]] > scores[ordered[1]]:
        return ordered[0]
    return None


KIDS = re.compile(r"\b(?:kids?|toddlers?|infants?|bab(?:y|ies)|girls?|boys?|child(?:ren)?|junior|youth)\b", re.I)
WOMEN = re.compile(r"\b(?:women(?:'s)?|woman|ladies|female)\b", re.I)
MEN = re.compile(r"\b(?:men(?:'s)?|man|unisex)\b", re.I)
MIXED_AGE = re.compile(r"\b(?:kids?|children) (?:and|&) adults?\b|\badults? (?:and|&) (?:kids?|children)\b", re.I)
# Receipt titles that omit an audience label; exact brand and SKU/line only.
WOMENS_LULU = re.compile(r"^(?:RePleat High-Rise Tennis Skirt|Daydrift High-Rise|lululemon Align™? High-Rise|"
                         r"InvisiWear Mid-Rise Bikini|Scuba (?:Oversized|High-Rise)|Softstreme Pintuck High-Rise)\b", re.I)


def exclude_item(item, merchant, urls=()):
    if item.audience in {'women', 'kids'}:
        return item.audience
    name = item.name.replace('’', "'")
    if merchant in {'Adidas','Lamoda'}:
        if re.search(r"\bдет(?:\.|ск\w*\b)", name, re.I):
            return 'kids'
        if re.search(r"\bжен(?:\.|ск\w*\b)", name, re.I) and not re.search(r"\bмуж(?:\.|ск\w*\b)|\bунисекс\b", name, re.I):
            return 'women'
    if KIDS.search(name) and not MIXED_AGE.search(name):
        return 'kids'
    if WOMEN.search(name) and not MEN.search(name):
        return 'women'
    if merchant == 'Tracksmith' and any(re.search(r"/[^/]+-Women(?:[s_'-]|-)", urlsplit(url).path, re.I) for url in urls):
        return 'women'
    if item.brand == 'lululemon':
        if WOMENS_LULU.search(name):
            return 'women'
        styles = {m[1] for url in urls if (m := re.match(r'https://images\.lululemon\.com/is/image/lululemon/L([MWU])',url))}
        if styles == {'W'}:
            return 'women'
    if merchant == 'Uniqlo' and item.sku in {'459794', '460921'}:
        return 'women'  # Women's Fluffy Yarn Fleece Jacket and Seamless Down Short Coat.
    if name.startswith('Dog Car Seat Cover,'):
        return 'non_apparel'
    return None
