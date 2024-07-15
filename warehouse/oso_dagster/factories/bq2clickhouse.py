from dataclasses import dataclass, field
from typing import Optional, Sequence, Dict, List, Tuple
from dagster import (
    asset,
    AssetExecutionContext,
    MaterializeResult,
)
from dagster_gcp import BigQueryResource, GCSResource
from google.cloud.bigquery import Client as BQClient
from .common import AssetFactoryResponse
from ..resources import ClickhouseResource
from ..utils.bq import BigQueryTableConfig, export_to_gcs
from ..utils.errors import UnsupportedTableColumn
from ..utils.gcs import gcs_to_http_url, batch_delete_folder
from ..utils.clickhouse import create_table, import_data, drop_table, rename_table
from ..utils.common import SourceMode

# This is the folder in the GCS bucket where we will stage the data
GCS_BUCKET_DIRECTORY = "bq2clickhouse"

@dataclass(kw_only=True)
class Bq2ClickhouseAssetConfig:
    # Dagster key prefix
    key_prefix: Optional[str | Sequence[str]] = ""
    # Dagster asset name
    asset_name: str
    # Unique ID for this sync
    sync_id: str
    # Source config from BigQuery
    source_config: BigQueryTableConfig
    # GCS bucket to stage the data
    staging_bucket: str
    # Destination table in Clickhouse
    destination_table_name: str
    # index_name => list of column names to index
    index: Optional[Dict[str, List[str]]]
    # Incremental or overwrite
    copy_mode: SourceMode
    # Dagster remaining args
    asset_kwargs: dict = field(default_factory=lambda: {})

# Map BigQuery column types to Clickhouse
COLUMN_MAP = {
    "STRING": "String",
    "FLOAT": "Float32",
    "FLOAT64": "Float64",
    "INTEGER": "Int64",
    "INT64": "Int64",
    "TIMESTAMP": "DateTime",
    "DATETIME": "DateTime",
    "DATE": "Date",
    "BYTES": "String",
    "BOOL": "Boolean",
    "BOOLEAN": "Boolean",
    "NUMERIC": "Decimal",
    "DECIMAL": "Decimal",
    "BIGNUMERIC": "Decimal256",
    "BIGDECIMAL": "Decimal256",
    "TIME": "DateTime",
    "JSON": "JSON",
}


def get_bq_table_columns(bq_client: BQClient, bq_table_config: BigQueryTableConfig) -> List[Tuple[str, str]]:
    """
    Get the columns of a BigQuery table as Clickhouse Types
    See https://clickhouse.com/docs/en/sql-reference/data-types

    Parameters
    ----------
    bq_client: BQClient
        BigQuery client
    bq_table_config: BigQueryTableConfig
        BigQuery table configuration
    
    Returns
    -------
    List[Tuple[str, str]]
        List of (name, type) pairs
    """
    columns: List[Tuple[str, str]] = []
    dataset_ref = bq_client.dataset(dataset_id=bq_table_config.dataset_name)
    table_ref = dataset_ref.table(bq_table_config.table_name)
    table = bq_client.get_table(table_ref)

    for f in table.schema:
        field_type = f.field_type
        if field_type in ["RECORD", "STRUCT"]:
            raise UnsupportedTableColumn(
                'Field "%s" has unsupported type "%s"' % (f.name, field_type)
            )
        column_type = COLUMN_MAP.get(field_type)
        if not column_type:
            raise UnsupportedTableColumn(
                'Field "%s" has unsupported type "%s"' % (f.name, field_type)
            )
        columns.append((f.name, column_type))
    return columns


def create_bq2clickhouse_asset(asset_config: Bq2ClickhouseAssetConfig):
    """
    This is a factory for creating a Dagster asset
    that copies a BigQuery table into Clickhouse
    """

    @asset(name=asset_config.asset_name, key_prefix=asset_config.key_prefix, **asset_config.asset_kwargs)
    def bq2clickhouse_asset(
        context: AssetExecutionContext,
        bigquery: BigQueryResource,
        clickhouse: ClickhouseResource,
        gcs: GCSResource,
    ) -> MaterializeResult:
        context.log.info(f"Materializing a Clickhouse asset called {asset_config.asset_name}")
        bq_source = asset_config.source_config
        gcs_bucket = asset_config.staging_bucket
        gcs_relative_dir = "%s/%s/%s" % (
            GCS_BUCKET_DIRECTORY,
            asset_config.sync_id,
            asset_config.destination_table_name
        )
        gcs_path = "gs://%s/%s" % (
            gcs_bucket,
            gcs_relative_dir,
        )
        context.log.debug(f"Exporting {bq_source.project_id}:{bq_source.dataset_name}.{bq_source.table_name} to {gcs_path}")

        # Export BigQuery table to GCS
        with bigquery.get_client() as bq_client:
            gcs_glob = export_to_gcs(bq_client, bq_source, gcs_path)
            columns = get_bq_table_columns(bq_client, bq_source)
            context.log.info(
                f"Exported {bq_source.project_id}:{bq_source.dataset_name}.{bq_source.table_name} to {gcs_glob}"
            )

        # Create the Clickhouse tables and import the data
        destination_table_name = asset_config.destination_table_name
        index = asset_config.index
        source_url = gcs_to_http_url(gcs_glob)
        with clickhouse.get_client() as ch_client:
            # Create a temporary table that we will use to write
            temp_dest = "%s_%s" % (destination_table_name, asset_config.sync_id.replace("-", "_"))
            if len(temp_dest) > 63:
                temp_dest = temp_dest[0:63].rstrip("_")
            # Also ensure that the expected destination exists. Even if we
            # will delete this keeps the `OVERWRITE` mode logic simple
            create_table(ch_client, destination_table_name, columns, index, if_not_exists=True)
            context.log.info(f"Ensured destination table {destination_table_name}")
            create_table(ch_client, temp_dest, columns, index, if_not_exists=False)
            context.log.info(f"Created temporary table {temp_dest}")
            import_data(ch_client, temp_dest, source_url)
            context.log.info(f"Imported {source_url} into {temp_dest}")
            drop_table(ch_client, destination_table_name)
            context.log.info(f"Dropped table: {destination_table_name}")
            rename_table(ch_client, temp_dest, destination_table_name)
            context.log.info(f"Moved {temp_dest} to {destination_table_name}")

        # Delete the gcs files
        gcs_client = gcs.get_client()
        batch_delete_folder(gcs_client, gcs_bucket, gcs_relative_dir)
        context.log.info(f"Deleted GCS folder {gcs_path}")

        return MaterializeResult(
            metadata={
                "success": True,
                "asset": asset_config.asset_name,
                "gcs_glob": gcs_glob,
                "clickhouse_temp_table": temp_dest,
            }
        )

    return AssetFactoryResponse([bq2clickhouse_asset])