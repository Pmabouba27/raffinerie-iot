import paho.mqtt.client as mqtt
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic
import json

KAFKA_BROKER = "localhost:9092"
TOPIC_NAME = "sensor-data"

# Création du topic Kafka si absent
try:
    admin_client = AdminClient({'bootstrap.servers': KAFKA_BROKER})
    topic_list = [NewTopic(TOPIC_NAME, num_partitions=1, replication_factor=1)]
    admin_client.create_topics(new_topics=topic_list)
except Exception as e:
    print(f"Erreur ou topic déjà existant : {e}")

producer = Producer({
    'bootstrap.servers': KAFKA_BROKER
})

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        producer.produce(TOPIC_NAME, value=json.dumps(data).encode('utf-8'))
        producer.flush()
        print(f"Envoyé à Kafka : {data}")
    except Exception as e:
        print(f"Erreur lors de la publication vers Kafka : {e}")

# CallbackAPIVersion.VERSION2 requis avec paho-mqtt >= 2.0
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect("localhost", 1883)
client.subscribe("raffinerie/temp")
client.subscribe("raffinerie/vib")
client.on_message = on_message

print("En attente de messages MQTT... (Ctrl+C pour arrêter)")
try:
    client.loop_forever()
except KeyboardInterrupt:
    print("\nBridge MQTT→Kafka arrêté.")
finally:
    producer.flush()
    client.disconnect()
