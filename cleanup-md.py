import re
from collections import defaultdict

local_toc = defaultdict(list)
dry_run = False
template = "---\ntitle: Las Recetas de Mami\n---\n"
new = [template]
path = []
nav = []
toc = []
first = False
for x in m[3:]:
    c = x.count('#')
    if 0 < c <= 3:
        if not first:
            first = True
        else:
            file_to_create = '../' + '/'.join(path) + ('/index.md' if len(path) < 3 else '.md')
            if dry_run:
                print(file_to_create)
            else:
                with open(file_to_create, 'w') as f:
                    f.writelines(new)

        path = path[:c-1]
        nav = nav[:c-1]
        
        title = x.split(' ',1)[1].strip().replace('.','').strip().replace(',','').strip().replace('"','').strip().replace('.','').strip().replace('“','').strip().replace('”','').strip()
        filename = re.sub("[\(\[].*?[\)\]]", "", title).strip().replace(' ','-').lower()
        path.append(filename)
        link = '[' + title + ']({{ site.baseurl }}/'+'/'.join(path) +')'
        
        for i in range(len(nav)):
            local_toc[nav[i]].append('    ' * i +'- ' + link)
            
        nav.append(link)
        toc.append('    ' * (x.count('#')-1) +'- ' + link)

        new = [template, ' > '.join(['[Home]({{ site.baseurl }})',] + nav) + '\n\n']
    new.append(x if not x.startswith('#') else x[len(path)-1:])
for t in toc:
    print(t)
