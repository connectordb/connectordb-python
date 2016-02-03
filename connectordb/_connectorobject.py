
class ConnectorObject(object):
    """Users, devices and streams are all built upon the base `ConnectorObject`.
    The methods from ConnectorObject can be accessed from any user, device or stream.

    Do not use this object directly. The API is accessed using the ConnectorDB class (below).
    """

    def __init__(self, database_connection, object_path):
        self.db = database_connection
        self.path = object_path

        # Metadata represents the object's json representation
        self.metadata = None

    def refresh(self):
        """Refresh reloads data from the server. It raises an error if it fails to get the object's metadata"""
        self.metadata = self.db.read(self.path).json()

    @property
    def data(self):
        """Returns the raw dict representing metadata"""
        if self.metadata is None:
            self.refresh()
        return self.metadata

    def delete(self):
        """Deletes the user/device/stream"""
        self.db.delete(self.path)

    def exists(self):
        """returns true if the object exists, and false otherwise. This is useful for creating streams
        if they exist::

            cdb = connectordb.ConnectorDB("myapikey")

            mystream = cdb["mystream"]

            if not mystream.exists():
                mystream.create({"type":"string"})

        """
        try:
            self.refresh()
        except:
            return False
        return True

    def set(self, property_dict):
        """Attempts to set the given properties of the object.
        An example of this is setting the nickname of the object::

            cdb.set({"nickname": "My new nickname"})

        note that there is a convenience property `cdb.nickname` that allows you to get/set the nickname directly.
        """
        self.metadata = self.db.update(self.path, property_dict).json()

    @property
    def name(self):
        """Returns the object's name. Object names are immutable (unless logged in is a database admin)"""
        return self.data["name"]

    @property
    def nickname(self):
        """Allows to directly set the object's user-friendly nickname.
        Usage is as a property::
            cdb.nickname = "My Nickname!"

            print cdb.nickname
        """
        if "nickname" in self.data:
            return self.data["nickname"]
        return None

    @nickname.setter
    def nickname(self, new_nickname):
        """Sets the object's user-friendly nickname"""
        self.set({"nickname": new_nickname})
