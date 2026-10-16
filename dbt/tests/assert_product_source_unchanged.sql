select item.id, item.product_name_source, bronze_item.name_source
from {{ ref('silver_slipapp_items') }} as item
join {{ source('bronze', 'slipapp_items') }} as bronze_item
  on bronze_item.id = item.bronze_slipapp_item_id
where item.product_name_source is distinct from bronze_item.name_source

union all

select input_entry.id, input_entry.product_name_source, bronze_input.product_name_source
from {{ ref('silver_input_entries') }} as input_entry
join {{ source('bronze', 'input_entries') }} as bronze_input
  on bronze_input.id = input_entry.bronze_input_id
where input_entry.product_name_source is distinct from bronze_input.product_name_source

union all

select product.id, product.name_source, product.name_source
from {{ ref('silver_products') }} as product
where not exists (
    select 1
    from {{ source('bronze', 'slipapp_items') }} as bronze_item
    where bronze_item.name_source = product.name_source
)
and not exists (
    select 1
    from {{ source('bronze', 'input_entries') }} as bronze_input
    where bronze_input.product_name_source = product.name_source
)
