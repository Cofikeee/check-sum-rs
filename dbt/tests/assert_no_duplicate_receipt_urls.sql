select receipt_url
from {{ ref('silver_slipapp_receipts') }}
where nullif(trim(receipt_url), '') is not null
group by receipt_url
having count(*) > 1
