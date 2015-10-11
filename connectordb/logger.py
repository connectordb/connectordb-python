from __future__ import absolute_import

import apsw  # The default python slite implementatino is not threadsafe, so we use apsw
import logging
import time
import threading

import json
from jsonschema import validate

from ._connectordb import ConnectorDB, CONNECTORDB_URL


class Logger(object):
    """Logger enables logging datapoints with periodic synchronization to a ConnectorDB database.
    the logged datapoints are cached in a sqlite database, as well as the necessary connection data,
    so that no data is lost, and settings don't need to be reloaded from the database after initial connection.
    """

    def __init__(self, database_file_path, on_create=None, apikey=None, onsyncfail=None):
        """Logger is started by passing its database file, and an optional callback which is run if the database
        is not initialized, allowing setup code to be only run once"""
        self.database = apsw.Connection(database_file_path)
        c = self.database.cursor()

        # Create the tables which will make up the cache if they don't exist yet
        c.execute(
            "CREATE TABLE IF NOT EXISTS cache (stream TEXT, timestamp REAL, jsondata TEXT);")
        c.execute(
            "CREATE TABLE IF NOT EXISTS streams (stream TEXT PRIMARY KEY, jsonschema TEXT);")
        c.execute(
            "CREATE TABLE IF NOT EXISTS metadata (apikey TEXT, serverurl TEXT, lastsynctime REAL, syncperiod REAL, userdatajson TEXT);")

        # Now check if there is already metadata in the table, and if not, insert new metadata,
        # and run the on_create callback
        c.execute("SELECT COUNT(*) FROM metadata;")
        row_number = c.next()[0]
        if row_number == 0:
            logging.debug("Logger: Creating new database")
            # The default values are as follows:
            # apikey: '' (needs to be set by user)
            # serverurl: connectordb.com
            # lastsynctime: 0 (never syncd)
            # syncperiod: 600 (10 minutes)
            # user data: {} (empty dict)
            c.execute("INSERT INTO metadata VALUES ('',?,0,600,'{}')",
                      (CONNECTORDB_URL, ))

        # Load the database metadata into variables
        c.execute(
            "SELECT apikey,serverurl,lastsynctime,syncperiod FROM metadata;")
        self.__apikey, self.__serverurl, self.__lastsync, self.__syncperiod = next(c)

        # Load the streams that are being logged
        c.execute("SELECT * FROM streams;")
        self.streams = {}
        for row in c.fetchall():
            self.streams[row[0]] = json.loads(row[1])

        if apikey is not None:
            self.apikey = apikey

        self.synclock = threading.Lock()
        self.syncthread = None
        self.__cdb = None

        # Callback that is called when synchronization fails
        self.onsyncfail = onsyncfail

        # Run the create callback which is for the user to set up the necessary
        # values to ensure a connection - only if the database was just created
        if on_create is not None and row_number == 0:
            on_create(self)

    @property
    def connectordb(self):
        """Returns the ConnectorDB object that the logger uses. Raises an error if Logger isn't able to connect"""
        if self.__cdb is None:
            logging.debug("Logger: Connecting to " + self.serverurl)
            self.__cdb = ConnectorDB(self.apikey, url=self.serverurl)
        return self.__cdb

    def close(self):
        """Closes the database connections and stops all synchronization."""
        self.stop()
        with self.synclock:
            self.database.close()

    def addStream(self, streamname, schema=None):
        """Adds the given stream to the logger. Requires an active connection to the ConnectorDB database.

        If a schema is not specified, loads the stream from the database. If a schema is specified, and the stream
        does not exist, creates the stream."""

        stream = self.connectordb[streamname]

        if not stream.exists():
            if schema is not None:
                stream.create(schema)
            else:
                raise Exception(
                    "The stream '%s' was not found" % (streamname, ))

        self.addStream_force(streamname, stream.schema)

    def addStream_force(self, streamname, schema=None):
        """This function adds the given stream to the logger, but does not check with a ConnectorDB database
        to make sure that the stream exists. Use at your own risk."""

        c = self.database.cursor()
        c.execute("INSERT OR REPLACE INTO streams VALUES (?,?);",
                  (streamname, json.dumps(schema)))

        self.streams[streamname] = schema

    def insert(self, streamname, value):
        """Insert the datapoint into the logger for the given stream name. The logger caches the datapoint
        and eventually synchronizes it with ConnectorDB"""
        if streamname not in self.streams:
            raise Exception("The stream '%s' was not found" % (streamname, ))

        # Validate the schema
        validate(value, self.streams[streamname])

        # Insert the datapoint - it fits the schema
        value = json.dumps(value)
        logging.debug("Logger: %s <= %s" % (streamname, value))
        c = self.database.cursor()
        c.execute("INSERT INTO cache VALUES (?,?,?);",
                  (streamname, time.time(), value))

    def sync(self):
        """Attempt to sync with the ConnectorDB server"""
        logging.debug("Logger: Syncing...")
        try:
            # Get the connectordb object
            cdb = self.connectordb

            # Ping the database - most connection errors will happen here
            cdb.ping()

            with self.synclock:
                c = self.database.cursor()
                for stream in self.streams:
                    s = cdb[stream]
                    if not s.exists():
                        raise Exception(
                            "Stream %s does not exist!" % (stream, ))

                    c.execute(
                        "SELECT * FROM cache WHERE stream=? ORDER BY timestamp ASC;",
                        (stream, ))
                    datapointArray = []
                    for dp in c.fetchall():
                        datapointArray.append(
                            {"t": dp[1],
                             "d": json.loads(dp[2])})
                    if len(datapointArray) > 0:
                        logging.debug("%s: syncing %i datapoints" %
                                      (stream, len(datapointArray)))

                        s.insert_array(datapointArray)

                        # If there was no error inserting, delete the datapoints from the cache
                        c.execute(
                            "DELETE FROM cache WHERE stream=? AND timestamp <=?",
                            (stream, datapointArray[-1]["t"]))
                self.lastsynctime = time.time()
        except:
            # Handle the sync failure callback
            reraise = True
            if self.onsyncfail is not None:
                reraise = self.onsyncfail(self)
            if reraise:
                raise

    def __setsync(self):
        with self.synclock:
            logging.debug("Next sync attempt in " + str(self.syncperiod))
            if self.syncthread is not None:
                self.syncthread.cancel()
            self.syncthread = threading.Timer(self.syncperiod,
                                              self.__runsyncer)
            self.syncthread.daemon = True
            self.syncthread.start()

    def __runsyncer(self):
        try:
            self.sync()
        except Exception and e:
            logging.warn("ConnectorDB sync failed: " + str(e))
        self.__setsync()

    def start(self):
        """Start the logger background synchronization service. This allows you to not need to
        worry about syncing with ConnectorDB - you just insert into the Logger, and the Logger
        will by synced every syncperiod."""

        # This makes sure a connection can be made to the database
        self.connectordb

        with self.synclock:
            if self.syncthread is not None:
                logging.warn(
                    "Logger: Start called on a syncer that is already running")
                return

        self.__setsync()

    def stop(self):
        """Stops the background synchronization thread"""
        with self.synclock:
            if self.syncthread is not None:
                self.syncthread.cancel()
                self.syncthread = None

    def __len__(self):
        """Returns the number of datapoints currently cached"""
        c = self.database.cursor()
        c.execute("SELECT COUNT() FROM cache;")
        return c.next()[0]

    def __contains__(self, streamname):
        """Returns whether the logger is caching the given stream name"""
        return streamname in self.streams

    @property
    def syncperiod(self):
        """Syncperiod is the time in seconds between attempting to synchronize with ConnectorDB.
        The Logger will gather all data in its sqlite database between sync periods, and every syncperiod
        seconds, it will attempt to connect to write the data to ConnectorDB."""
        return self.__syncperiod

    @syncperiod.setter
    def syncperiod(self, value):
        resync = False
        with self.synclock:
            self.__syncperiod = value
            resync = self.syncthread is not None
        c = self.database.cursor()
        c.execute("UPDATE metadata SET syncperiod=?", (value, ))

        if resync:
            self.__setsync()  # If we change the sync period during runtime, immediately update

    @property
    def lastsynctime(self):
        """The timestamp of the most recent successful synchronization with the server"""
        return self.__lastsync

    @lastsynctime.setter
    def lastsynctime(self, value):
        self.__lastsync = value
        c = self.database.cursor()
        c.execute("UPDATE metadata SET lastsynctime=?", (value, ))

    @property
    def apikey(self):
        """The API key used to connect to ConnectorDB. This needs to be set before the logger can do anything!
        The apikey only needs to be set once, since it is stored in the Logger database.

        Note that changing the api key is not supported during logger runtime (after start is called).
        Logger must be recreated for a changed apikey to come into effect."""
        return self.__apikey

    @apikey.setter
    def apikey(self, value):
        self.__apikey = value
        c = self.database.cursor()
        c.execute("UPDATE metadata SET apikey=?", (value, ))

    @property
    def serverurl(self):
        """The URL of the ConnectorDB server that Logger is using. By default this is connectordb.com, but can
        be set with this property. Note that the property will only take into effect before runtime"""
        return self.__serverurl

    @serverurl.setter
    def serverurl(self, value):
        self.__serverurl = value
        c = self.database.cursor()
        c.execute("UPDATE metadata SET serverurl=?", (value, ))

    @property
    def data(self):
        """The data property allows the user to save settings/data in the database, so that
        there does not need to be extra code messing around with settings.

        Use this property to save things that can be converted to JSON inside the logger database,
        so that you don't have to mess with configuration files or saving setting otherwise::

            from connectordb.logger import Logger

            l = Logger("log.db")

            l.data = {"hi": 56}

            # prints the data dictionary
            print l.data
        """
        c = self.database.cursor()
        c.execute("SELECT userdatajson FROM metadata;")
        return json.loads(c.next()[0])

    @data.setter
    def data(self, value):
        c = self.database.cursor()
        c.execute("UPDATE metadata SET userdatajson=?;", (json.dumps(value), ))

    @property
    def name(self):
        """Gets the name of the currently logged in device"""
        return self.connectordb.ping()
