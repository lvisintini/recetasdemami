import re
import os
import uuid
import json

from utils import slugify, clean

# https://chatlyai.app/chat/70a8b3ea-4665-44a3-a46a-f389ae340ddb

with open('./Recetas.md', 'r') as f:
    m = list(f.readlines())

with open("recetas_paths.json", encoding="utf-8") as f:
    claude_paths = json.load(f)

index_template="""---
slug: {slug}
uuid: {uuid}
page_title: {dirty_title}
---
{nav}

## {dirty_title}

{toc}

<TODO>
"""

template = """---
slug: {slug}
uuid: {uuid}
page_title: {dirty_title}
---

{nav}

## {dirty_title}

|**Crédito(s):**| |
|**Tiempo de Preparación Estimado:**| |
|**Tiempo de Cocción Estimado:**| |
|**Raciones:**| |

{summary}

### Ingredientes

- 
- 
- 

### Preparación

{content}

### Variantes


<TODO>
"""
content = []
path = []
first = False
missing = []

for x in m:
    x = x.strip()
    c = x.count('#')
    if 0 < c <= 3:
        if not first:
            first = True
        else:
            directory = './docs/' + '/'.join(path)
            if len(path) < 3:
                #if not os.path.exists(directory):
                #     os.makedirs(directory)
                file_to_create = directory + '/index.md'
            else:
                file_to_create = directory + '.md'
            
            if os.path.exists(file_to_create):
                raise Exception('May be overriding data', file_to_create)
                
            if (file_to_create.endswith("index.md") and content != ['',]):
                print(file_to_create, content)
            
            if not file_to_create.endswith("index.md"):
                md_file = path[-1] + '.md'
                claude_path = claude_paths.get(md_file)
                if claude_path is None:
                    missing.append(md_file)
                else:
                    claude_path_parts = claude_path.split('/')
                    for i in range(1, len(claude_path_parts)):
                        path_to_create = './' + '/'.join([slugify(clean(p)) for p in claude_path_parts[:i]])
                        os.makedirs(path_to_create, exist_ok=True)
                        if not os.path.exists(path_to_create+'/index.md'):
                            with open(path_to_create+'/index.md', 'w') as f:
                                f.write(index_template.format(
                                    dirty_title=claude_path_parts[i-1],
                                    slug="index",
                                    uuid=str(uuid.uuid4()),
                                    nav='{nav}',
                                    toc='{toc}',
                                ))

                    abs_path = path_to_create + '/' + md_file
                
                    with open(abs_path, 'w') as f:
                        f.write(template.format(
                            clean_title=clean_title,
                            dirty_title=dirty_title,
                            slug=filename,
                            uuid=str(uuid.uuid4()),
                            nav='{nav}',
                            content='\n'.join(content),
                            summary="{summary}"
                        ))

        path = path[:c-1]
        clean_title = clean(x)
        dirty_title = x.split(' ',1)[1].strip()
        filename = slugify(clean_title)
        path.append(filename)
            
        content = []
    else:
        content.append(x if not x.startswith('#') else x[len(path)-1:])

directory = './docs/' + '/'.join(path)
if len(path) < 3:
    #if not os.path.exists(directory):
    #     os.makedirs(directory)
    file_to_create = directory + '/index.md'
else:
    file_to_create = directory + '.md'

if os.path.exists(file_to_create):
    raise Exception('May be overriding data', file_to_create)
    
if (file_to_create.endswith("index.md") and content != ['',]):
    print(file_to_create, content)
    
if not file_to_create.endswith("index.md"):
    md_file = path[-1] + '.md'
    claude_path = claude_paths.get(md_file)
    if claude_path is None:
        missing.append(md_file)
    else:
        claude_path_parts = claude_path.split('/')
        for i in range(1, len(claude_path_parts)):
            path_to_create = './' + '/'.join([slugify(clean(p)) for p in claude_path_parts[:i]])
            os.makedirs(path_to_create, exist_ok=True)
            if not os.path.exists(path_to_create+'/index.md'):
                with open(path_to_create+'/index.md', 'w') as f:
                    f.write(index_template.format(
                        dirty_title=claude_path_parts[i-1],
                        slug="index",
                        uuid=str(uuid.uuid4()),
                        nav='{nav}',
                        toc='{toc}',
                    ))
        
        abs_path = path_to_create + '/' + md_file

        with open(abs_path, 'w') as f:
            f.write(template.format(
                clean_title=clean_title,
                dirty_title=dirty_title,
                slug=filename,
                uuid=str(uuid.uuid4()),
                nav='{nav}',
                content='\n'.join(content),
                summary="{summary}"
            ))
            
for ms in missing:
    print(ms)
