{{ config(
    alias='slipapp_receipts',
    unique_key='id'
) }}

with ranked as (
    select
        receipt.*,
        coalesce(
            nullif(trim(receipt.qr_url), ''),
            'PFR:' || nullif(trim(receipt.fiscal_receipt_number), ''),
            'ID:' || receipt.id::text
        ) as dedupe_key,
        row_number() over (
            partition by coalesce(
                nullif(trim(receipt.qr_url), ''),
                'PFR:' || nullif(trim(receipt.fiscal_receipt_number), ''),
                'ID:' || receipt.id::text
            )
            order by receipt.id
        ) as row_number
    from {{ source('bronze', 'slipapp_receipts') }} as receipt
),
canonical as (
    select * from ranked where row_number = 1
)
select
    {{ sha256_hex('dedupe_key') }} as id,
    {{ sha256_hex('dedupe_key') }} as receipt_key,
    canonical.id as bronze_slipapp_receipt_id,
    merchant.id as merchant_id,
    merchant.name_source as merchant_name,
    receipt_datetime,
    currency,
    gross_total,
    tax_total,
    {{ normalize_text('coalesce(payment_override.english_text, canonical.payment_method)') }} as payment_method,
    fiscal_receipt_number,
    fiscal_esir,
    qr_url as receipt_url,
    'READY' as source_status,
    current_timestamp as updated_at
from canonical
left join {{ ref('silver_merchants') }} as merchant
    on merchant.name_normalized = {{ normalize_company_name('canonical.merchant_name_source') }}
left join {{ source('meta', 'translation_overrides') }} as payment_override
    on payment_override.source_normalized = lower(trim(canonical.payment_method))
