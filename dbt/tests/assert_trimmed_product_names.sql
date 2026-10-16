select product_key, name_source
from {{ ref('silver_products') }}
where name_source ~ '^[[:space:]]*\[[0-9]+\]'
   or name_source ~ '^[[:space:]]*[0-9]+[[:space:]]+'
   or name_source ~* '[[:space:]]*/[[:space:]]*(KOM|КОМ|PCS|KG|КГ|G|Г|L|Л|SET|M)[.]?[[:space:]]*$'
   or name_source ~* '[[:space:]]*\[(KOM|КОМ|PCS|KG|КГ|G|Г|L|Л|SET|M)\][.]?[[:space:]]*$'
   or name_en ~ '>>[^<]+<<'
