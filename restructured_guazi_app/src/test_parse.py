import sys
sys.path.insert(0, r'e:\project\zhikuan\guazi_app_data_system\restructured_guazi_app\src')
from guazi_core.device_operations import parse_nodes, all_labels

path = r'e:\project\zhikuan\guazi_app_data_system\restructured_guazi_app\output\debug_series_081424.xml'
with open(path, 'r', encoding='utf-8') as f:
    xml = f.read()

nodes = parse_nodes(xml)
print(f'Total nodes: {len(nodes)}')
for n in nodes[:30]:
    print(f"text={n['text']!r}, desc={n['content_desc']!r}, labels={n['labels']}")

labels = all_labels(nodes)
print(f'Total labels: {len(labels)}')
for l in labels[:30]:
    print(repr(l))
