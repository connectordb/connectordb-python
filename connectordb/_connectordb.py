from __future__ import absolute_import

from ._connection import DatabaseConnection

from ._device import Device
from ._user import User
from ._stream import Stream

CONNECTORDB_URL = "https://connectordb.com"


class ConnectorDB(Device):
    """ConnectorDB is the main entry point for any application that uses the python API.
    The class accepts both a username and password in order to log in as a user, and accepts an apikey
    when logging in directly from a device::

        import connectordb
        cdb = connectordb.ConnectorDB("myusername","mypassword")

        #prints "myusername/user" - logging in by username/password combo
        #logs in as the user device.
        print cdb.path

    """
    def __init__(self, user_or_apikey, user_password=None, url=CONNECTORDB_URL):

        db = DatabaseConnection(user_or_apikey, user_password, url)

        Device.__init__(self, db, db.ping())

    def __call__(self, path):
        """Enables getting arbitrary users/devices/streams in a simple way. Just call the object
        with the u/d/s uri
            cdb = ConnectorDB("myapikey")
            cdb("user1") -> user1 object
            cdb("user1/device1") -> user1/device1 object
            cdb("user1/device1/stream1") -> user1/device1/stream1 object
        """
        n = path.count("/")
        if n == 0:
            return User(self.db, path)
        elif n == 1:
            return Device(self.db, path)
        else:
            return Stream(self.db, path)

    def close(self):
        """shuts down all active connections to ConnectorDB"""
        self.db.close()

    def count_users(self):
        """Gets the total number of users registered with the database. Only available to administrator."""
        return int(self.db.get("", {"q": "countusers"}).text)

    def count_devices(self):
        """Gets the total number of devices registered with the database. Only available to administrator."""
        return int(self.db.get("", {"q": "countdevices"}).text)

    def count_streams(self):
        """Gets the total number of streams registered with the database. Only available to administrator."""
        return int(self.db.get("", {"q": "countstreams"}).text)

    def info(self):
        """returns a dictionary of information about the database, including the database version, the transforms
        and the interpolators supported::

            >>>cdb = connectordb.ConnectorDB(apikey)
            >>>cdb.info()
            {
                "version": "0.3.0",
                "transforms": {
                    "sum": {"description": "Returns the sum of all the datapoints that go through the transform"}
                    ...
                },
                "interpolators": {
                    "closest": {"description": "Uses the datapoint closest to the interpolation timestamp"}
                    ...
                }
            }

        """
        return {
            "version": self.db.get("meta/version").text,
            "transforms": self.db.get("meta/transforms").json(),
            "interpolators": self.db.get("meta/interpolators").json()
        }

    def __repr__(self):
        return "[ConnectorDB:%s]" % (self.path, )

    def ping(self):
        """Pings the ConnectorDB server. Useful for checking if the connection is valid"""
        return self.db.ping()
