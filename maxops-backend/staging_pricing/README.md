Tooling for building and unpacking the bundled AWS pricing database.

`import_vantage_pricing.py` pulls AWS pricing data from the Vantage website JSON assets and writes the curated regional subset into `maxops_pricing.db`.

`import_s3_pricing.py` reads the AWS Pricing API at release time and replaces
the selected regions in the `street_pricing_s3` table. It uses the same
15-region `TARGET_REGIONS` list as the Vantage importer; add a new region to
that list before rerunning it. For example:
`./.venv/bin/python3.14 -m staging_pricing.import_s3_pricing --profile maxops --database ./maxops_pricing.db`.

Repo-bundled workflow:

1. Build or refresh the DB:
   `./.venv/bin/python3.14 staging_pricing/import_vantage_pricing.py --db-path ./maxops_pricing.db`
2. Enrich the global EC2 instance-spec catalog with structured network and EBS
   capabilities fetched once from `us-east-1`:
   `./.venv/bin/python3.14 staging_pricing/enrich_ec2_instance_specs.py --database ./maxops_pricing.db`
3. Import the reviewed RDS hardware and exact class/storage Price List extracts:
   `./.venv/bin/python3.14 staging_pricing/import_rds_rightsizer_catalog.py --database ./maxops_pricing.db --specs ./rds_specs.json --class-prices ./rds_class_prices.json --storage-prices ./rds_storage_prices.json`
4. Package the bundled repo artifact:
   `./.venv/bin/python3.14 staging_pricing/package_pricing_release.py --db-path ./maxops_pricing.db`
5. Commit the generated files from `pricing_artifacts/`:
   - `maxops_pricing.db.xz`
   - `maxops_pricing.db.xz.sha256`
   - `maxops_pricing.metadata.json`
6. OSS users unpack the DB locally with no network access:
   `./.venv/bin/python3.14 staging_pricing/unpack_pricing_db.py`

The enrichment calls `DescribeInstanceTypes` only in `us-east-1`, not
`DescribeInstanceTypeOfferings`. Instance specifications are treated as global,
region-invariant product data keyed by instance type; prices remain regional.
Types not returned by `us-east-1` remain explicit coverage gaps and safely use
the rightsizer's assumed/unknown capability paths. The enrichment provides
reliable numeric network baseline/peak values and EBS attachment limits without
making Availability Zone launch-capacity claims.
