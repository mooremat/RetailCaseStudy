# This is written for Python 2.7

# Run script with full load:
# spark-submit --packages mysql:mysql-connector-java:5.1.39,org.apache.spark:spark-avro_2.11:2.4.0 part1.py F

# Run script with incremental load:
# spark-submit --packages mysql:mysql-connector-java:5.1.39,org.apache.spark:spark-avro_2.11:2.4.0 part1.py I

from pyspark import SparkContext
from pyspark.sql import SparkSession, SQLContext
import datetime
import time
import sys
import boto3
import os
import tempfile

creds = "/home/msr/mysql_creds"
# File to save last update time, will move this to S3 later
last_update = "mysql_last_update"
bucket_name = "rcs-training-12-18"
sc = SparkContext("local[2]", "Case-Study-Part-1")
skip_key = "config_files/skip"


# Just to show the each section of the program in the middle of all the output
def section_header(h):
    print "\n\n\n"
    print "----------"+h+"----------"
    print "\n\n\n"


# Load in table names into an array that is returned
def table_names():
    section_header("Get table names from S3")
    s3 = boto3.resource('s3')
    bucket = s3.Bucket(bucket_name)
    t_names = []
    for obj in bucket.objects.all():
        key = obj.key
        key_parts = key.split("/")
        if key_parts[0] == "config_files" and key_parts[1] == "tables":
            f = tempfile.NamedTemporaryFile(delete=False)
            f.write(obj.get()['Body'].read())
            f.close()
            file = open(f.name, 'r')
            for l in file:
                t_names.append(l.rstrip())
    return t_names


def get_last_update_time():
    section_header("Get table names from S3")
    s3 = boto3.resource('s3')
    bucket = s3.Bucket(bucket_name)
    for obj in bucket.objects.all():
        key = obj.key
        key_parts = key.split("/")
        if key_parts[0] == "config_files" and key_parts[1] == last_update:
            return int(obj.get()['Body'].read().rstrip())
    print "Failed to load file:" + last_update
    exit(1)


def update_last_update_time(t):
    s3_resource = boto3.resource('s3')
    client = boto3.client('s3')
    s3_resource.Object(bucket_name, 'config_files/' + last_update).delete()
    client.put_object(Bucket=bucket_name, Key="config_files/" + last_update, Body=str(t).encode())
    return


def mysql_creds():
    f = open(creds, 'r')
    user = f.readline().rstrip()
    password = f.readline().rstrip()
    url = f.readline().rstrip()
    f.close()
    return user, password, url


# Loads a dataframe and returns it
# If the load is incremental, removes old data
def load_df(table_name, incremental, ts):
    sqlcontext = SQLContext(sc)
    u, pw, url = mysql_creds()
    df = sqlcontext.read.format("jdbc").option("url", url).option("driver", "com.mysql.jdbc.Driver").option(
          "dbtable", table_name).option("user", u).option("password", pw).load()
    if incremental == 'I':
        return df.filter(df.last_update > ts)
    else:
        return df


# Writes the dataframe to S3 using boto3
# Saves the data as an avro
def write_avro2s3(dfs, table_order, incremental):
    section_header("Writing Avro to S3")
    client = boto3.client('s3')
    s3 = boto3.resource('s3')
    s3.Object(bucket_name, skip_key).delete()
    write_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    skipped = 0
    for i in range(len(dfs)):
        df = dfs[i]
        if df.count() == 0 and incremental == 'I':
            skipped += 1
            continue
        dir_name = table_order[i]
        path = os.path.join(tempfile.mkdtemp(), dir_name)
        df.repartition(1).write.format("avro").save(path)
        parts = 0
        for f in os.listdir(path):
            if f.startswith('part'):
                out = path + "/" + f
                client.put_object(Bucket=bucket_name, Key="raw/" + dir_name + "/" + write_time + str(incremental) + "/"
                                                          + "%05d.arvo" % (parts,), Body=open(out, 'r'))
                parts += 1
    if skipped == len(dfs) and incremental == 'I':
        client.put_object(Bucket=bucket_name, Key=skip_key, Body="")


def main(arg):
    # If no arguments in command line, exit the program with an error
    if len(arg) < 1:
        print "No command line arguments."
        exit(1)
    if arg[0] != 'I' and arg[0] != 'F':
        print "Bad argument. Use 'I' or 'F'"
        exit(1)
    # Try to read the unix timestamp from the file listed in the variable "last_update"
    if arg[0] == 'I':
        last_update_unix_ts = get_last_update_time()
    else:
        last_update_unix_ts = 0
    # Used when updating the "last_update" file
    new_update_time = int(time.time())
    dfs = []
    t_names = table_names()
    # Load the dataframes into a list
    section_header("Load Dataframes")
    for n in t_names:
        dfs.append(load_df(n, arg[0], last_update_unix_ts))
    # Update the "last_update" file with the newest runtime.
    update_last_update_time(new_update_time)
    # Saves the dataframes as avro files in S3
    write_avro2s3(dfs, t_names, arg[0])


# Runs the script
if __name__ == "__main__":
    section_header("Program Start")
    main(sys.argv[1:])
