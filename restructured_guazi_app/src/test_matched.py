import xml.etree.ElementTree as ET

path = r'e:\project\zhikuan\guazi_app_data_system\restructured_guazi_app\output\debug_matched_brand_083335.xml'
with open(path, 'rb') as f:
    raw = f.read()

# Search for 东风 in UTF-8
for brand in ['东风', '东风风神', '东风风光', '东风风行', '东风汽车']:
    b = brand.encode('utf-8')
    idx = raw.find(b)
    if idx >= 0:
        print(f'Found {brand!r} at index {idx}')
    else:
        print(f'NOT found: {brand!r}')

# Search for any label containing 东
root = ET.fromstring(raw)
for node in root.iter('node'):
    text = node.attrib.get('text', '')
    cd = node.attrib.get('content-desc', '')
    if '东' in text or '东' in cd:
        print(f'Found 东: text={text!r}, desc={cd!r}')
        print(f"  bounds: {node.attrib.get('bounds', '')}")
        print()
