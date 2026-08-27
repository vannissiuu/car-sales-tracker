import csv, json
from collections import defaultdict, Counter

# ---- 1. json.load ----
with open('/tmp/brand-mapping/mapping.json', encoding='utf-8') as f:
    data = json.load(f)
print("=== 1. json.load ===")
print("OK. top-level keys:", list(data.keys()))

m2b = data['manufacturer_to_brand']
mod2b = data['model_to_brand']
uncertain = data['uncertain']

with open('/tmp/full/data/manufacturers.txt') as f:
    mans = [l.strip() for l in f if l.strip()]

# ---- 2. 117 manufacturers all in manufacturer_to_brand, uncertain empty ----
print("\n=== 2. manufacturer coverage ===")
missing = [m for m in mans if m not in m2b]
extra = [m for m in m2b if m not in mans]
print("manufacturers.txt count:", len(mans))
print("manufacturer_to_brand count:", len(m2b))
print("missing:", missing)
print("extra:", extra)
print("uncertain array:", uncertain, "(len", len(uncertain), ")")
assert not missing and not extra and len(m2b) == 117 and uncertain == []
print("PASS")

# ---- 3. the 4 mandated manufacturers: 100% of distinct models covered by model_to_brand ----
print("\n=== 3. model_to_brand coverage for the 4 mandated manufacturers ===")
targets4 = ["长城汽车", "蔚来", "上汽集团", "奇瑞捷豹路虎"]
models_by_man = defaultdict(set)
with open('/tmp/full/data/sales.csv') as f:
    r = csv.DictReader(f)
    rows = list(r)
    for row in rows:
        if row['manufacturer'] in targets4:
            models_by_man[row['manufacturer']].add(row['model'])

total_n = 0
total_covered = 0
for t in targets4:
    ms = sorted(models_by_man[t])
    covered = [x for x in ms if x in mod2b]
    not_covered = [x for x in ms if x not in mod2b]
    total_n += len(ms)
    total_covered += len(covered)
    print(f"{t}: covered {len(covered)}/{len(ms)}", "MISSING:" if not_covered else "", not_covered)
print(f"\nTOTAL across 4 mandated manufacturers: covered {total_covered}/{total_n}")
assert total_covered == total_n, "NOT 100% covered!"
print("PASS: 100% coverage confirmed")

# ---- also report the bonus manufacturers (星途/北京汽车制造厂/江汽集团/江铃集团新能源) ----
print("\n=== 3b. bonus multi-brand manufacturers (not mandated, best-effort) ===")
bonus = ["星途", "北京汽车制造厂", "江汽集团", "江铃集团新能源"]
models_by_man2 = defaultdict(set)
for row in rows:
    if row['manufacturer'] in bonus:
        models_by_man2[row['manufacturer']].add(row['model'])
for t in bonus:
    ms = sorted(models_by_man2[t])
    covered = [x for x in ms if x in mod2b]
    default_via_manufacturer = [x for x in ms if x not in mod2b]
    print(f"{t}: {len(covered)} models overridden via model_to_brand, {len(default_via_manufacturer)} fall back to manufacturer_to_brand['{t}']='{m2b[t]}':", default_via_manufacturer)

# ---- 4. resolve_brand + full pass over sales.csv ----
print("\n=== 4. resolve_brand over all 18814 rows ===")
def resolve_brand(manufacturer, model, mapping):
    m2b_ = mapping['manufacturer_to_brand']
    mod2b_ = mapping['model_to_brand']
    if model in mod2b_:
        return mod2b_[model], 'model'
    if manufacturer in m2b_:
        return m2b_[manufacturer], 'manufacturer'
    return manufacturer, 'fallback_raw'

hit_model = 0
hit_manufacturer = 0
hit_fallback = 0
brand_totals = defaultdict(int)
fallback_rows = []
for row in rows:
    brand, source = resolve_brand(row['manufacturer'], row['model'], data)
    sales = int(row['sales'])
    brand_totals[brand] += sales
    if source == 'model':
        hit_model += 1
    elif source == 'manufacturer':
        hit_manufacturer += 1
    else:
        hit_fallback += 1
        fallback_rows.append((row['manufacturer'], row['model']))

print(f"total rows: {len(rows)}")
print(f"hit model_to_brand: {hit_model}")
print(f"hit manufacturer_to_brand: {hit_manufacturer}")
print(f"fallback to raw manufacturer value (should be 0): {hit_fallback}")
if fallback_rows:
    print("fallback examples:", fallback_rows[:20])
assert hit_model + hit_manufacturer + hit_fallback == len(rows)
assert hit_fallback == 0, "There ARE rows falling back to raw manufacturer value!"
print("PASS: 0 fallback rows")

print(f"\ndistinct brands after full resolution: {len(brand_totals)}")

print("\n=== Brand sales Top 20 (full resolved) ===")
ranked = sorted(brand_totals.items(), key=lambda x: -x[1])
for i, (b, s) in enumerate(ranked[:20]):
    print(f"{i+1}\t{b}\t{s}")

# ---- 5. specific assertions ----
print("\n=== 5. specific case assertions ===")
cases = [
    ('长城汽车', '哈弗H6', '哈弗'),
    ('长城汽车', '坦克300', '坦克'),
    ('上汽集团', 'MG4', 'MG'),
    ('上汽大众', '朗逸', '大众'),
]
for man, mod, expected in cases:
    got, source = resolve_brand(man, mod, data)
    status = "OK" if got == expected else "FAIL"
    print(f"resolve_brand('{man}','{mod}') = '{got}' (via {source}) -- expected '{expected}' -- {status}")
    assert got == expected

print("\nALL ASSERTIONS PASSED")

# sanity: total sales conservation
total_all = sum(int(row['sales']) for row in rows)
print(f"\ntotal sales (raw sum): {total_all}")
print(f"total sales (sum of brand_totals): {sum(brand_totals.values())}")
assert total_all == sum(brand_totals.values())
print("Sales conserved after remapping: PASS")
