"""Match Apple's published configuration links; never manufacture part numbers."""
import re
from urllib.parse import urlsplit

from app.models.config import apple_url, money


SKU_LINK_SCRIPT = '''els=>els.map(a=>({
  url:a.href.split('?')[0].split('#')[0],
  color:a.querySelector('.dimensionColor')?.textContent.trim() || '',
  capacity:(a.querySelector('.dimensionCapacity')?.textContent || '').match(/^\\s*(\\d+\\s*(?:GB|TB))/i)?.[1] || '',
  price:a.querySelector('.current_price')?.textContent.trim() || ''
})).filter(a=>a.color && a.capacity && a.price)'''


def normalized(value):
    return re.sub(r'\s+', '', str(value)).casefold()


def family_url(url):
    if not apple_url(url):
        return ''
    parsed = urlsplit(url)
    match = re.fullmatch(r'(/shop/buy-iphone/iphone-[a-z0-9-]+)(?:/[a-z0-9]{5,16}/a)?/?', parsed.path, re.I)
    return f'https://{parsed.hostname}{match[1]}' if match else ''


def variants_from_links(offer, links):
    """The public capacity price also disambiguates Pro from Pro Max links."""
    groups = {g['key']: g['choices'] for g in offer.get('groups', [])}
    colors, capacities = groups.get('dimensionColor', []), groups.get('dimensionCapacity', [])
    candidates = {}
    for link in links:
        url = link.get('url', '')
        if (family_url(url) != family_url(offer.get('url', '')) or not family_url(url)
                or url.rstrip('/') == family_url(url) or urlsplit(url).query or urlsplit(url).fragment):
            continue
        color = [c for c in colors if normalized(c['label']) == normalized(link.get('color', ''))]
        capacity = [c for c in capacities if normalized(c['label']) == normalized(link.get('capacity', ''))]
        price = re.fullmatch(r'RMB\s*([\d,]+(?:\.\d+)?)', link.get('price', ''))
        if len(color) != 1 or len(capacity) != 1 or not price or not capacity[0].get('price'):
            continue
        amount = str(money(price[1]))
        if amount != str(money(capacity[0]['price'])):
            continue
        options = {**offer.get('locked_options', {}), 'dimensionColor': color[0]['value'],
                   'dimensionCapacity': capacity[0]['value']}
        key = tuple(sorted(options.items()))
        candidates.setdefault(key, {})[url] = {'url': url, 'options': options, 'price': amount}
    # Conflicting links for the same full configuration are not a usable shortcut.
    return [next(iter(links.values())) for links in candidates.values() if len(links) == 1]


def selected_variant(offer, options):
    if not options:
        return None
    matches = [v for v in offer.get('variants', []) if v.get('options') == options
               and family_url(v.get('url', '')) == family_url(offer.get('url', ''))
               and family_url(v.get('url', '')) and v['url'].rstrip('/') != family_url(v['url'])
               and not urlsplit(v['url']).query and not urlsplit(v['url']).fragment]
    return matches[0] if len(matches) == 1 else None
