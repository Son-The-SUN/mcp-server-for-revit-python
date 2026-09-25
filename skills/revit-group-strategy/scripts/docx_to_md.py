"""Extract the text of a Word document (paragraphs with heading levels, lists and tables) to Markdown.

Used to read the Revit Execution Plan when pandoc is not installed:

    python docx_to_md.py "Revit Execution Plan.docx" rep.md

Prints the line count and the embedded media files (e.g. the workset list screenshot).
"""
import sys, zipfile, re
import xml.etree.ElementTree as ET
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
path = sys.argv[1]
out = sys.argv[2]
z = zipfile.ZipFile(path)
xml = z.read('word/document.xml')
root = ET.fromstring(xml)
body = root.find(W+'body')
# style id -> name
styles = {}
try:
    sroot = ET.fromstring(z.read('word/styles.xml'))
    for s in sroot.findall(W+'style'):
        sid = s.get(W+'styleId')
        n = s.find(W+'name')
        styles[sid] = n.get(W+'val') if n is not None else sid
except Exception:
    pass
def ptext(p):
    parts = []
    for node in p.iter():
        if node.tag == W+'t' and node.text:
            parts.append(node.text)
        elif node.tag == W+'tab':
            parts.append('\t')
        elif node.tag == W+'br':
            parts.append('\n')
    return ''.join(parts)
def pstyle(p):
    ppr = p.find(W+'pPr')
    if ppr is None: return ''
    ps = ppr.find(W+'pStyle')
    num = ppr.find(W+'numPr')
    st = styles.get(ps.get(W+'val'), ps.get(W+'val')) if ps is not None else ''
    return st + (' [list]' if num is not None else '')
lines = []
for el in body:
    if el.tag == W+'p':
        t = ptext(el)
        st = pstyle(el)
        if not t.strip():
            continue
        m = re.match(r'heading (\d)', st.lower())
        if m:
            lines.append('#'*int(m.group(1)) + ' ' + t)
        elif st.lower().startswith('title'):
            lines.append('# ' + t)
        elif '[list]' in st or 'list' in st.lower():
            lines.append('- ' + t)
        else:
            lines.append(t)
    elif el.tag == W+'tbl':
        lines.append('')
        for i, tr in enumerate(el.findall(W+'tr')):
            cells = []
            for tc in tr.findall(W+'tc'):
                ct = ' / '.join(ptext(p).strip() for p in tc.findall('.//'+W+'p') if ptext(p).strip())
                cells.append(ct.replace('|', r'\|'))
            lines.append('| ' + ' | '.join(cells) + ' |')
            if i == 0:
                lines.append('|' + '---|'*len(cells))
        lines.append('')
with open(out, 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(lines))
print('lines', len(lines))
print('media:', [n for n in z.namelist() if n.startswith('word/media')])
