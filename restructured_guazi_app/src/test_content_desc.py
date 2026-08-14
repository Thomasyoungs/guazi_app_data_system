import xml.etree.ElementTree as ET

path = r'e:\project\zhikuan\guazi_app_data_system\restructured_guazi_app\output\debug_pre_click_brand_082650.xml'
with open(path, 'rb') as f:
    raw = f.read()

root = ET.fromstring(raw)

# Find nodes with content-desc containing 东
for node in root.iter('node'):
    cd = node.attrib.get('content-desc', '')
    if cd and '东' in cd:
        print(f'Found content-desc: {cd!r}')
        print(f"  text: {node.attrib.get('text', '')!r}")
        print(f"  bounds: {node.attrib.get('bounds', '')!r}")
        print()
