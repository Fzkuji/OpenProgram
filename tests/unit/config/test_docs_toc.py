from xml.etree import ElementTree as ET

from scripts.docs_site import build
from scripts.docs_site.build import extract_toc


def _toc(source):
    return ET.fromstring('<root>' + extract_toc(source) + '</root>')


def test_toc_groups_children_under_their_nearest_shallower_heading():
    root = _toc('''<h2 id="part">Part</h2><h3 id="first">First</h3>
    <h4 id="detail">Detail</h4><h3 id="second">Second</h3>
    <h2 id="next">Next</h2><h3 id="third">Third</h3>''')
    parts = root.findall('./ul/li')
    assert [part.find('./details/summary/a').get('href') for part in parts] == ['#part', '#next']
    assert parts[0].find('./details/ul/li/details/summary/a').get('href') == '#first'
    assert parts[0].find('./details/ul/li/a').get('href') == '#second'
    assert parts[0].find('./details/ul/li/details/ul/li/a').get('href') == '#detail'
    assert parts[1].find('./details/ul/li/a').get('href') == '#third'
    assert [link.get('href') for link in root.iter('a')] == [
        '#part', '#first', '#detail', '#second', '#next', '#third',
    ]


def test_toc_handles_skipped_levels_without_empty_parent_groups():
    root = _toc('''<h3 id="orphan">Orphan</h3><h6 id="deep">Deep</h6>
    <h2 id="parent">Parent</h2><h5 id="skip">Skip</h5>''')
    assert [link.get('href') for link in root.findall('./ul/li/details/summary/a')] == ['#orphan', '#parent']
    assert [link.get('href') for link in root.findall('./ul/li/details/ul/li/a')] == ['#deep', '#skip']
    assert len(root.findall('.//ul')) == 3


def test_toc_preserves_escaped_labels_and_anchors_and_skips_empty_headings():
    root = _toc('''<h1 id="title">Title</h1><h2 id="a&amp;b">A &amp; <code>B</code>
    <a class="header-anchor" href="#a&amp;b">#</a></h2><h3 id="empty"></h3>''')
    links = list(root.iter('a'))
    assert len(links) == 1
    assert links[0].text.strip() == 'A & B'
    assert links[0].get('href') == '#a&b'
    assert extract_toc('<h1>Only a title</h1>') == ''


def test_markdown_deep_headings_have_targets_without_renaming_existing_anchors(monkeypatch):
    monkeypatch.setattr(build, '_SLUG_DEDUP', {})
    body = build.make_md().render('''## Parent
### Child
#### Duplicate
##### Detail
###### Leaf
## Duplicate
''')
    article = ET.fromstring('<article>' + body + '</article>')
    root = _toc(body)
    assert article.find('h2[2]').get('id') == 'duplicate'
    assert article.find('h4').get('id') == 'duplicate-1'
    assert root.find('./ul/li/details/ul/li/details/ul/li/details/ul/li/details/ul/li/a').get('href') == '#leaf'
    targets = {heading.get('id') for heading in article}
    assert all(link.get('href')[1:] in targets for link in root.iter('a'))


def test_only_parent_headings_are_disclosures_with_separate_navigation_links():
    root = _toc('<h2 id="parent">Parent</h2><h3 id="child">Child</h3><h2 id="leaf">Leaf</h2>')
    disclosure = root.find('./ul/li/details')
    assert disclosure is not None and 'open' in disclosure.attrib
    assert disclosure.find('./summary/a').get('href') == '#parent'
    assert disclosure.find('./ul/li/a').get('href') == '#child'
    assert len(root.findall('.//details')) == 1
    assert root.find('./ul/li/a').get('href') == '#leaf'
