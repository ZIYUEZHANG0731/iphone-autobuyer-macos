import hashlib
import json
from app.models.config import money


def quote_key(product, amount):
    fields=[product.name,product.url,product.options,product.quantity,str(money(amount))]
    return hashlib.sha256(json.dumps(fields,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
