from datetime import datetime
from unittest.mock import ANY, patch

from cloudevents.http import CloudEvent
from kafka.consumer.fetcher import ConsumerRecord
from pytest import mark

from acringest import app, put_data


@patch("minio.Minio")
def test_put_data(mc_client):
    mc = mc_client()
    put_data(mc, bucket="bucket", acrid="acrid", data="{}")
    mc.put_object.assert_called_with(
        "bucket",
        "acrid",
        ANY,
        length=ANY,
        content_type="application/json",
    )


@mark.parametrize(
    "event,expected",
    [
        (
            CloudEvent(
                {
                    "id": "x-amz-request-id.x-amz-id-2",
                    "source": "minio:s3..acrcloud.raw",
                    "specversion": "1.0",
                    "type": "com.amazonaws.s3.s3:ObjectCreated:Put",
                    "datacontenttype": "application/json",
                    "subject": "objectkey",
                    "time": "eventtime",
                },
                {
                    "responseElements": {
                        "x-amz-request-id": "x-amz-request-id",
                        "x-amz-id-2": "x-amz-id-2",
                    },
                    "eventSource": "eventsource",
                    "awsRegion": "",
                    "s3": {
                        "bucket": {"name": "bucketname"},
                        "object": {"key": "objectkey"},
                    },
                    "eventName": "eventname",
                    "eventTime": "eventtime",
                },
            ),
            {},
        )
    ],
)
@patch("acringest.Minio")
@patch("acringest.from_structured")
@patch("acringest.KafkaConsumer")
def test_app(mock_consumer, mock_from_structured, mc_client, event, expected):
    mock_consumer.side_effect = lambda *_, **__: [
        ConsumerRecord(
            topic="test",
            partition=0,
            offset=0,
            timestamp=datetime(1993, 3, 1),
            timestamp_type=None,
            key="testkey",
            value="{}",
            headers=None,
            checksum=None,
            serialized_key_size=0,
            serialized_value_size=0,
            serialized_header_size=0,
        )
    ]
    mock_from_structured.return_value = event
    mc_client.return_value = mc_client
    mc_client.get_object.return_value = mc_client
    mc_client.json.return_value = [
        {
            "metadata": {
                "music": [
                    {
                        "acrid": "acrid",
                        "163": "ignored",
                    }
                ]
            }
        }
    ]
    app(
        bootstrap_servers="server:9092",
        security_protocol="SSL",
        tls_cafile=None,
        tls_certfile=None,
        tls_keyfile=None,
        consumer_topic="ctopic",
        consumer_group="cgroup",
        consumer_auto_offset_reset="creset",
        minio_url="play.min.io:9000",
        minio_access_key="",
        minio_secret_key="",
        minio_bucket_raw="raw",
        minio_bucket_music="music",
        minio_secure="",
        minio_cert_reqs="",
        minio_ca_certs="",
    )
    mc_client.put_object.assert_called_once_with(
        "music", "acrid", ANY, length=18, content_type="application/json"
    )
