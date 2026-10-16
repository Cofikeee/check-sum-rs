{% macro category_wide(product_expression, merchant_expression) -%}
case
    when lower({{ product_expression }}) ~ '(milk|mleko|jogurt|sir|cheese|butter|puter|pavlaka|dairy|ml[ečc])' then 'FOOD'
    when lower({{ product_expression }}) ~ '(soap|sapun|deterg|clean|cist|toilet|paper|household|domac)' then 'HOUSEHOLD'
    when lower({{ product_expression }}) ~ '(shampoo|sampon|tooth|pasta|cosmetic|care|deodor|krem)' then 'PERSONAL CARE'
    when lower({{ product_expression }}) ~ '(apple|jabuk|banana|orange|pomor|fruit|voce|voće)' then 'FOOD'
    when lower({{ product_expression }}) ~ '(tomato|paradaj|potato|krompir|onion|luk|vegetable|povr)' then 'FOOD'
    when lower({{ product_expression }}) ~ '(meat|meso|chicken|pilet|beef|junet|pork|svinj|sausage|kobas)' then 'FOOD'
    when lower({{ product_expression }}) ~ '(bread|hleb|kruh|bakery|pek)' then 'FOOD'
    when lower({{ product_expression }}) ~ '(coffee|kafa|tea|čaj|caj|water|voda|juice|sok|beer|pivo|wine|vino|drink)' then 'BEVERAGES'
    when lower({{ merchant_expression }}) ~ '(restaurant|restoran|cafe|kaf|bar)' then 'FOOD SERVICE'
    else 'OTHER'
end
{%- endmacro %}

{% macro category_narrow(product_expression, merchant_expression) -%}
case
    when lower({{ product_expression }}) ~ '(milk|mleko|jogurt|sir|cheese|butter|puter|pavlaka|dairy|ml[ečc])' then 'DAIRY'
    when lower({{ product_expression }}) ~ '(apple|jabuk|banana|orange|pomor|fruit|voce|voće)' then 'FRUIT'
    when lower({{ product_expression }}) ~ '(tomato|paradaj|potato|krompir|onion|luk|vegetable|povr)' then 'VEGETABLES'
    when lower({{ product_expression }}) ~ '(meat|meso|chicken|pilet|beef|junet|pork|svinj|sausage|kobas)' then 'MEAT'
    when lower({{ product_expression }}) ~ '(bread|hleb|kruh|bakery|pek)' then 'BAKERY'
    when lower({{ product_expression }}) ~ '(coffee|kafa|tea|čaj|caj|water|voda|juice|sok|beer|pivo|wine|vino|drink)' then 'DRINKS'
    when lower({{ product_expression }}) ~ '(soap|sapun|deterg|clean|cist|toilet|paper|household|domac)' then 'HOUSEHOLD'
    when lower({{ product_expression }}) ~ '(shampoo|sampon|tooth|pasta|cosmetic|care|deodor|krem)' then 'PERSONAL CARE'
    when lower({{ merchant_expression }}) ~ '(restaurant|restoran|cafe|kaf|bar)' then 'RESTAURANT'
    else 'OTHER'
end
{%- endmacro %}
