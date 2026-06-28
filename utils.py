import re

def clean(value):
    cleaned = value.split(' ',1)[1].strip().replace('.','').strip().replace(',','').strip().replace('"','').strip().replace('.','').strip().replace('“','').strip().replace('”','').strip()
    cleaned = re.sub("[\(\[].*?[\)\]]", "", cleaned).strip()
    return cleaned

def slugify(value):
    return value.replace(' ','-').lower()

