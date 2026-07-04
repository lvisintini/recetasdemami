import re

def clean(value):
    cleaned = value.split(' ',1)[1].strip().replace('.','').strip().replace(',','').strip().replace('"','').strip().replace('.','').strip().replace('“','').strip().replace('”','').strip()
    cleaned = re.sub("[\(\[].*?[\)\]]", "", cleaned).strip()
    return cleaned

def slugify(value):
    cleanded = value.replace('ó','o').replace('ú','u').replace('í','i').replace('á','a').replace('é','e').replace('ñ','n').replace('´','').replace('â','a').replace('Ñ', 'N')
    return cleanded.replace(' ','-').lower()

