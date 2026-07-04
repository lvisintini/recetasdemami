import re
import os
import uuid

from utils import slugify, clean

with open('./Recetas.md', 'r') as f:
    m = list(f.readlines())

template = """---
title: {title}
subtitle: {subtitle}
slug: {slug}
uuid: {uuid}
---
{nav}

{content}
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
                        title=title,
                        subtitle="{subtitle}",
                        slug=filename,
                        uuid=str(uuid.uuid4()),
                        nav='{nav}',
                        content='\n'.join(content)
                    ))

        path = path[:c-1]
        title = clean(x)
        filename = slugify(title)
        path.append(filename)
            
        content = []
    content.append(x if not x.startswith('#') else x[len(path)-1:])
