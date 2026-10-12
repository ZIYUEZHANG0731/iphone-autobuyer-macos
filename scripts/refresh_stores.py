"""Refresh only Apple's public mainland retail directory; no browser/account."""
import argparse
import json
from pathlib import Path
import sys
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.stores import DIRECTORY_URL, parse_directory


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--html', type=Path, help='Previously fetched official HTML')
    parser.add_argument('--output', type=Path, default=Path('app/services/official_stores.json'))
    args = parser.parse_args()
    html = args.html.read_text(encoding='utf-8') if args.html else urlopen(DIRECTORY_URL, timeout=30).read().decode('utf-8')
    data = parse_directory(html)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f"已保存 {len(data['stores'])} 家 Apple 中国大陆直营店，{len({s['city'] for s in data['stores']})} 个城市。")
