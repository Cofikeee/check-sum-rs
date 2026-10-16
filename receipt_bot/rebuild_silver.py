from __future__ import annotations

import argparse
import json

from .category import classifier_from_environment
from .config import Settings
from .dbt_runner import DbtRunner
from .translation import CachedTranslator, LocalMarianTranslator
from .warehouse import WarehouseRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild silver tables from all bronze SlipApp imports")
    parser.parse_args()
    settings = Settings.from_environment()
    settings.ensure_directories()
    repository = WarehouseRepository(settings.database_url)
    repository.initialise()
    dbt_runner = DbtRunner()
    if dbt_runner.available:
        dbt_runner.build()
        print(json.dumps({"dbt": "built", "counts": repository.layer_counts()}, ensure_ascii=False, indent=2))
        return
    translator = CachedTranslator(repository, LocalMarianTranslator(settings.translation_model))
    classifier = classifier_from_environment()
    rebuilt = sum(
        repository.build_silver_slipapp(import_id, translator, classifier)
        for import_id in repository.bronze_slipapp_import_ids()
    )
    print(json.dumps({"rebuilt_silver_receipts": rebuilt, "counts": repository.layer_counts()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
