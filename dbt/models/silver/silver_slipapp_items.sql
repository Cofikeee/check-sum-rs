{{ config(
    alias='slipapp_items',
    unique_key='bronze_slipapp_item_id'
) }}

with canonical_items as (
    select
        receipt.id as silver_receipt_id,
        receipt.receipt_key,
        receipt.merchant_id,
        receipt.merchant_name,
        item.id as bronze_slipapp_item_id,
        item.position,
        item.name_source,
        item.quantity,
        item.unit,
        item.unit_price,
        item.line_total,
        item.producer_source,
        {{ trim_product_metadata(natural_product_source('item.name_source')) }} as clean_name_source,
        {{ natural_product_source('item.name_source') }} as natural_name_source
    from {{ source('bronze', 'slipapp_items') }} as item
    join {{ ref('silver_slipapp_receipts') }} as receipt
        on receipt.bronze_slipapp_receipt_id = item.bronze_receipt_id
),
with_product as (
    select
        canonical_items.*,
        product.id as product_id,
        product.name_en as product_name,
        product.category_wide,
        product.category_narrow,
        product.category_source,
        product.category_confidence
    from canonical_items
    left join {{ ref('silver_products') }} as product
        on product.product_key = md5(
            coalesce(canonical_items.merchant_name, '') || '|' || canonical_items.clean_name_source
        )
)
select
    md5(receipt_key || ':' || position::text) as item_key,
    silver_receipt_id,
    bronze_slipapp_item_id,
    position,
    merchant_id,
    merchant_name,
    product_id,
    product_name,
    clean_name_source as product_name_source,
    quantity,
    unit,
    unit_price,
    line_total,
    nullif({{ normalize_text('producer_source') }}, '') as producer,
    category_wide,
    category_narrow,
    category_source,
    category_confidence
from with_product
