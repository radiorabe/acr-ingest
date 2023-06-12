import json
import logging
import signal
import sys
from io import BytesIO

import urllib3
from cloudevents.kafka import from_structured
from cloudevents.kafka.conversion import KafkaMessage
from configargparse import ArgumentParser
from jsondiff import diff
from kafka import KafkaConsumer
from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)

_ACR_INVALID_KEYS = [
    "163",
    "nct",
    "amco",
    "andyou",
    "anghami",
    "artstis",
    "awa",
    "base_score",
    "bugs",
    "deezer",
    "disco_3453684",
    "joox",
    "kkbox",
    "lyricfind",
    "merlin",
    "musicbrainz",
    "musicstory",
    "mwg",
    "nct",
    "omusic",
    "partner",
    "partners",
    "qqmusic",
    "rec_times",
    "rights_owners",
    "sme",
    "Soundcharts",
    "spotify",
    "trackitdown",
    "umg",
    "wmg",
    "works_ids",
    "youtube",
    "updated_at",
]
"""
These keys were empirically found based on our date.
"""

_ACR_IGNORED_KEYS = [
    "play_offset_ms",
    "sample_begin_time_offset_ms",
    "sample_end_time_offset_ms",
    "db_begin_time_offset_ms",
    "db_end_time_offset_ms",
    "duration_ms",
    "rec_times",
    "result_from",
    "base_score",
    "score",
    "ppm",
    "rights_claim_policy",
    "rights_claim",
    "release_by_territories",
    "langs",
] + _ACR_INVALID_KEYS
"""
These keys are part of a music entry but not relevant for our purposes.
"""

_ACR_EXPECTED_KEYS = [
    "album",
    "title",
    "source",
    "release_date",
    "genres",
    "label",
    "artists",
    "external_ids",
    "acrid",
    "external_metadata",
    "language",
    "contributors",
    "lyrics",
    "bpm",
    "exids",
    "works",
] + _ACR_IGNORED_KEYS
"""
All keys not in this list are a surprise and warrant further investigation.
"""


def put_data(mc: Minio, bucket: str, acrid: str, data: str):
    _as_bytes = data.encode("utf-8")
    mc.put_object(
        bucket,
        acrid,
        BytesIO(_as_bytes),
        length=len(_as_bytes),
        content_type="application/json",
    )


def app(
    bootstrap_servers: list[str],
    security_protocol: str,
    tls_cafile: str,
    tls_certfile: str,
    tls_keyfile: str,
    consumer_topic: str,
    consumer_group: str,
    consumer_auto_offset_reset: str,
    minio_url: str,
    minio_access_key: str,
    minio_secret_key: str,
    minio_bucket_raw: str,
    minio_bucket_music: str,
    minio_secure: bool,
    minio_cert_reqs: str,
    minio_ca_certs: str,
):
    consumer = KafkaConsumer(
        consumer_topic,
        bootstrap_servers=bootstrap_servers,
        security_protocol=security_protocol,
        group_id=consumer_group,
        auto_offset_reset=consumer_auto_offset_reset,
        ssl_cafile=tls_cafile,
        ssl_certfile=tls_certfile,
        ssl_keyfile=tls_keyfile,
    )

    def on_sigint(*_):  # pragma: no cover
        consumer.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_sigint)

    mc = Minio(
        minio_url,
        minio_access_key,
        minio_secret_key,
        secure=minio_secure,
        http_client=urllib3.PoolManager(
            cert_reqs=minio_cert_reqs, ca_certs=minio_ca_certs
        ),
    )
    for bucket in [minio_bucket_raw, minio_bucket_music]:
        if not mc.bucket_exists(bucket):  # pragma: no cover
            mc.make_bucket(bucket)

    for msg in consumer:
        ce = from_structured(
            message=KafkaMessage(
                key=msg.key,
                value=msg.value,
                headers=msg.headers if msg.headers else {},
            )
        )
        if (
            ce["source"] == "minio:s3..acrcloud.raw"
            and ce["type"] == "com.amazonaws.s3.s3:ObjectCreated:Put"
        ):
            bucket = ce.data.get("s3", {}).get("bucket", {}).get("name")
            name = ce.data.get("s3", {}).get("object", {}).get("key")
            obj = mc.get_object(bucket, name)
            for data in obj.json():
                for music in data.get("metadata", {}).get("music", []):
                    acrid = music.get("acrid")
                    for key in list(music.keys()):
                        if key not in _ACR_EXPECTED_KEYS:  # pragma: no cover
                            logger.error(f"Unexpected key {key} in acr results")
                    for to_del in _ACR_IGNORED_KEYS:
                        if to_del in music.keys():
                            del music[to_del]
                    minio_data = None
                    try:
                        minio_data = mc.get_object(minio_bucket_music, acrid).json()
                    except S3Error as ex:  # pragma: no cover
                        if ex.code == "NoSuchKey":
                            put_data(mc, minio_bucket_music, acrid, json.dumps(music))
                            minio_data = music
                    changes = diff(minio_data, music)
                    if changes:
                        put_data(mc, minio_bucket_music, acrid, json.dumps(music))
                        logger.info(f"Applied changes to {acrid=}: {changes}")


def main():  # pragma: no cover
    parser = ArgumentParser(__name__)
    parser.add(
        "--kafka-bootstrap-servers",
        required=True,
        env_var="KAFKA_BOOTSTRAP_SERVERS",
    )
    parser.add(
        "--kafka-security-protocol",
        default="PLAINTEXT",
        env_var="KAFKA_SECURITY_PROTOCOL",
    )
    parser.add(
        "--kafka-tls-cafile",
        default=None,
        env_var="KAFKA_TLS_CAFILE",
    )
    parser.add(
        "--kafka-tls-certfile",
        default=None,
        env_var="KAFKA_TLS_CERTFILE",
    )
    parser.add(
        "--kafka-tls-keyfile",
        default=None,
        env_var="KAFKA_TLS_KEYFILE",
    )
    parser.add(
        "--kafka-consumer-topic",
        default="cloudevents",
        env_var="KAFKA_CONSUMER_TOPIC",
    )
    parser.add(
        "--kafka-consumer-group",
        default=__name__,
        env_var="KAFKA_CONSUMER_GROUP",
    )
    parser.add(
        "--kafka-consumer-auto-offset-reset",
        default="latest",
        env_var="KAFKA_CONSUMER_AUTO_OFFSET_RESET",
    )
    parser.add(
        "--minio-url",
        default="minio.service.int.rabe.ch:9000",
        env_var="MINIO_HOST",
        help="MinIO Hostname",
    )
    parser.add(
        "--minio-secure",
        default=True,
        env_var="MINIO_SECURE",
        help="MinIO Secure param",
    )
    parser.add(
        "--minio-cert-reqs",
        default="CERT_REQUIRED",
        env_var="MINIO_CERT_REQS",
        help="cert_reqs for urlib3.PoolManager used by MinIO",
    )
    parser.add(
        "--minio-ca-certs",
        default="/etc/pki/ca-trust/extracted/openssl/ca-bundle.trust.crt",
        env_var="MINIO_CA_CERTS",
        help="ca_certs for urlib3.PoolManager used by MinIO",
    )
    parser.add(
        "--minio-bucket-raw",
        default="acrcloud.raw",
        env_var="MINIO_BUCKET_RAW",
        help="MinIO Bucket with raw ACRCloud data",
    )
    parser.add(
        "--minio-bucket-music",
        default="acrcloud.music",
        env_var="MINIO_BUCKET_MUSIC",
        help="MinIO Bucket with music ACRCloud data",
    )
    parser.add(
        "--minio-access-key",
        default=None,
        env_var="MINIO_ACCESS_KEY",
        help="MinIO Access Key",
    )
    parser.add(
        "--minio-secret-key",
        default=None,
        env_var="MINIO_SECRET_KEY",
        help="MinIO Secret Key",
    )
    parser.add(
        "--quiet", "-q", default=False, action="store_true", env_var="ACRINGEST_QUIET"
    )

    options = parser.parse_args()

    if not options.quiet:
        logging.basicConfig(level=logging.INFO)
    logger.info(f"Starting {__name__}...")

    app(
        bootstrap_servers=options.kafka_bootstrap_servers,
        security_protocol=options.kafka_security_protocol,
        tls_cafile=options.kafka_tls_cafile,
        tls_certfile=options.kafka_tls_certfile,
        tls_keyfile=options.kafka_tls_keyfile,
        consumer_topic=options.kafka_consumer_topic,
        consumer_group=options.kafka_consumer_group,
        consumer_auto_offset_reset=options.kafka_consumer_auto_offset_reset,
        minio_url=options.minio_url,
        minio_access_key=options.minio_access_key,
        minio_secret_key=options.minio_secret_key,
        minio_bucket_raw=options.minio_bucket_raw,
        minio_bucket_music=options.minio_bucket_music,
        minio_secure=options.minio_secure,
        minio_cert_reqs=options.minio_cert_reqs,
        minio_ca_certs=options.minio_ca_certs,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
