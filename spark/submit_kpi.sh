#!/bin/bash
# Script de lancement du job Spark KPI
# IMPORTANT : les packages doivent être sur une seule ligne sans espaces,
# sinon Spark ne parse que le premier et lève un NullPointerException.

PACKAGES="org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,org.postgresql:postgresql:42.6.0,org.apache.hadoop:hadoop-aws:3.3.4"

/opt/spark/bin/spark-submit \
  --conf spark.jars.ivy=/tmp/.ivy2 \
  --packages "$PACKAGES" \
  /app/traitement_kpi.py
