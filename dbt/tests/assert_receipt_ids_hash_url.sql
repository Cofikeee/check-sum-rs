select id, receipt_url
from {{ ref('silver_slipapp_receipts') }}
where nullif(trim(receipt_url), '') is not null
  and id <> encode(digest(convert_to(trim(receipt_url), 'UTF8'), 'sha256'), 'hex')
