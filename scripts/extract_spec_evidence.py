from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import hashlib,json

root=Path(__file__).resolve().parents[2]
out=root/'mold-agent/docs/erp-audit';out.mkdir(parents=True,exist_ok=True)
manifest=[]
ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
for filename,key in [('模具项目全流程管理系统需求规格说明书_V1.1.docx','requirements-v1.1'),('模具项目智能工作台技术开发文档_V3.6.docx','technical-v3.6')]:
    path=root/'outputs'/filename
    with ZipFile(path) as archive:doc=ET.fromstring(archive.read('word/document.xml'))
    lines=[];paragraphs=tables=0
    for node in doc.find('w:body',ns):
        if node.tag.endswith('}p'):
            text=''.join(node.itertext()) if False else ''.join(t.text or '' for t in node.findall('.//w:t',ns))
            if text:lines.append(text);paragraphs+=1
        elif node.tag.endswith('}tbl'):
            tables+=1
            for row in node.findall('w:tr',ns):
                cells=[' / '.join(''.join(t.text or '' for t in p.findall('.//w:t',ns)) for p in cell.findall('w:p',ns)) for cell in row.findall('w:tc',ns)]
                lines.append(' | '.join(cells))
    (out/(key+'.txt')).write_text('\n'.join(lines),encoding='utf-8')
    manifest.append({'file':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'paragraphs':paragraphs,'tables':tables,'lines':len(lines)})
(out/'documents.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(manifest,ensure_ascii=False))
