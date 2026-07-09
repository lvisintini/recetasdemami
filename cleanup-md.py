import re
import os
import uuid

from utils import slugify, clean

# https://chatlyai.app/chat/70a8b3ea-4665-44a3-a46a-f389ae340ddb

with open('./Recetas.md', 'r') as f:
    m = list(f.readlines())

template = """---
title: {clean_title}
subtitle: {subtitle}
slug: {slug}
uuid: {uuid}
---
{nav}

# {dirty_title}

Summary

|**Crédito(s):**| |
|**Tiempo de Preparación Estimado:**| |
|**Tiempo de Cocción Estimado:**| |
|**Raciones:**| |

## Ingredientes

- 
- 
- 

## Preparación

{content}

## Variantes


"""
content = []
path = []
first = False

for x in m:
    x = x.strip()
    c = x.count('#')
    if 0 < c <= 3:
        if not first:
            first = True
        else:
            directory = './docs/' + '/'.join(path)
            if len(path) < 3:
                if not os.path.exists(directory):
                     os.makedirs(directory)
                file_to_create = directory + '/index.md'
            else:
                file_to_create = directory + '.md'
            
            if os.path.exists(file_to_create):
                raise Exception('May be overriding data', file_to_create)
            
            if (file_to_create.endswith("index.md") and content[2:]) or not file_to_create.endswith("index.md"):
                with open(file_to_create, 'w') as f:
                    f.write(template.format(
                        clean_title=clean_title,
                        dirty_title=dirty_title,
                        subtitle="{subtitle}",
                        slug=filename,
                        uuid=str(uuid.uuid4()),
                        nav='{nav}',
                        content='\n'.join(content),
                    ))

        path = path[:c-1]
        clean_title = clean(x)
        dirty_title = x.split(' ',1)[1].strip()
        filename = slugify(clean_title)
        path.append(filename)
            
        content = []
    else:
        content.append(x if not x.startswith('#') else x[len(path)-1:])
