from pyspark.sql import SparkSession
from pyspark.sql.functions import col, from_json, window, avg, expr, to_timestamp
from pyspark.sql.types import StructType, StringType, FloatType, IntegerType

# 1. Définir le schéma attendu pour les messages JSON
# Compatible avec le simulateur réaliste (6 types de capteurs)
schema = StructType() \
    .add("capteur_id", IntegerType()) \
    .add("identifiant", StringType()) \
    .add("machine_id",  StringType()) \
    .add("zone",        StringType()) \
    .add("valeur",      FloatType())  \
    .add("unite",       StringType()) \
    .add("qualite",     StringType()) \
    .add("mode",        StringType()) \
    .add("timestamp",   StringType()) \
    .add("type_capteur", StringType())

# 2. Démarrer la session Spark
spark = SparkSession.builder \
    .appName("raffinerie-iot") \
    .config("spark.hadoop.fs.s3a.access.key", "minio") \
    .config("spark.hadoop.fs.s3a.secret.key", "minio123") \
    .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000") \
    .config("spark.hadoop.fs.s3a.path.style.access", "true") \
    .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem") \
    .getOrCreate()

# 3. Lire les données en streaming depuis Kafka
# - startingOffsets=latest : repart du dernier message disponible si le checkpoint est absent ou invalide
# - failOnDataLoss=false   : évite le crash si des offsets du checkpoint n'existent plus dans Kafka
#   (arrive quand le topic est recréé ou que les messages ont expiré entre deux lancements)
df = spark.readStream.format("kafka") \
    .option("kafka.bootstrap.servers", "kafka:29092") \
    .option("subscribe", "sensor-data") \
    .option("startingOffsets", "latest") \
    .option("failOnDataLoss", "false") \
    .load()

# 4. Décoder les messages JSON
json_df = df.selectExpr("CAST(value AS STRING)") \
    .select(from_json(col("value"), schema).alias("data")) \
    .select("data.*")

# 5. Convertir correctement le champ timestamp
json_df = json_df.withColumn("timestamp", to_timestamp("timestamp"))

# 6. Sauvegarde brute sur MinIO
json_df.writeStream \
    .format("json") \
    .option("path", "s3a://raffinerie-raw/raw") \
    .option("checkpointLocation", "s3a://raffinerie-raw/checkpoint_raw") \
    .outputMode("append") \
    .start()

# 7. Filtrer les données valides — 6 types de capteurs de la raffinerie
# Plages physiques réelles définies dans le simulateur réaliste
filtrees = json_df.filter(
    ((col("type_capteur") == "Température")  & (col("valeur").between(0, 500)))   |
    ((col("type_capteur") == "Pression")     & (col("valeur").between(0, 100)))   |
    ((col("type_capteur") == "Débit")        & (col("valeur").between(0, 5000)))  |
    ((col("type_capteur") == "Vibration")    & (col("valeur").between(0, 50)))    |
    ((col("type_capteur") == "Niveau")       & (col("valeur").between(0, 100)))   |
    ((col("type_capteur") == "H2S gazeux")   & (col("valeur").between(0, 1000)))
)

# 8. Fonction batch pour enregistrer les mesures filtrées dans TimescaleDB
def save_filtrees_to_pg(batch_df, batch_id):
    batch_df.write \
        .format("jdbc") \
        .option("url", "jdbc:postgresql://timescaledb:5432/iotdb") \
        .option("dbtable", "mesures_filtrees") \
        .option("user", "admin") \
        .option("password", "admin") \
        .option("driver", "org.postgresql.Driver") \
        .mode("append") \
        .save()

# 9. Écriture en base TimescaleDB des mesures filtrées
filtrees.writeStream \
    .foreachBatch(save_filtrees_to_pg) \
    .option("checkpointLocation", "/app/data/checkpoint_filtrees") \
    .outputMode("append") \
    .start()

# 10. Calcul des KPI : moyenne glissante sur 1 minute
# timestamp est déjà de type TimestampType (converti ligne 34), on l'utilise directement
kpi = filtrees.withColumn("ts", col("timestamp")) \
    .withWatermark("ts", "30 seconds") \
    .groupBy(window("ts", "1 minute"), "type_capteur") \
    .agg(avg("valeur").alias("valeur")) \
    .withColumn("type_kpi", col("type_capteur")) \
    .withColumn("unite", expr("""
        CASE type_capteur
            WHEN 'Température' THEN '°C'
            WHEN 'Pression'    THEN 'bar'
            WHEN 'Débit'       THEN 'm³/h'
            WHEN 'Vibration'   THEN 'mm/s'
            WHEN 'Niveau'      THEN '%'
            WHEN 'H2S gazeux'  THEN 'ppm'
            ELSE 'N/A'
        END
    """)) \
    .selectExpr("window.start as timestamp", "type_kpi", "valeur", "unite")

# 11. Fonction batch pour enregistrer les KPI dans TimescaleDB
def save_kpi_to_pg(batch_df, batch_id):
    batch_df.write \
        .format("jdbc") \
        .option("url", "jdbc:postgresql://timescaledb:5432/iotdb") \
        .option("dbtable", "kpi_indicateurs") \
        .option("user", "admin") \
        .option("password", "admin") \
        .option("driver", "org.postgresql.Driver") \
        .mode("append") \
        .save()

# 12. Écriture en base TimescaleDB des KPI
kpi.writeStream \
    .foreachBatch(save_kpi_to_pg) \
    .option("checkpointLocation", "/app/data/checkpoint_kpi") \
    .outputMode("append") \
    .start() \
    .awaitTermination()
