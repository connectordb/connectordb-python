from __future__ import absolute_import

from ._connectorobject import ConnectorObject

from jsonschema import Draft4Validator
import json


def query_maker(t1=None, t2=None, limit=None, i1=None, i2=None, transform=None):
    """query_maker takes the optional arguments and constructs a json query for a stream's
    datapoints using it::
        #{"t1": 5, "transform": "if $ > 5"}
        print query_maker(t1=5,transform="if $ > 5")
    """
    params = {}
    if t1 is not None:
        params["t1"] = t1
    if t2 is not None:
        params["t2"] = t2
    if limit is not None:
        params["limit"] = limit
    if i1 is not None or i2 is not None:
        if len(params) > 0:
            raise AssertionError(
                "Stream cannot be accessed both by index and by timestamp at the same time.")
        if i1 is not None:
            params["i1"] = i1
        if i2 is not None:
            params["i2"] = i2

    if transform is not None:
        params["transform"] = transform
    return params


class Stream(ConnectorObject):
    def create(self, schema):
        """Creates a stream given a JSON schema encoded as a python dict"""
        Draft4Validator.check_schema(schema)
        self.metadata = self.db.create(self.path, schema).json()

    def insert_array(self, datapoint_array, restamp=False):
        """given an array of datapoints, inserts them to the stream. This is different from insert(),
        because it requires an array of valid datapoints, whereas insert only requires the data portion
        of the datapoint, and fills out the rest::

            s = cdb["mystream"]
            s.create({"type": "number"})

            s.insert_array([{"d": 4, "t": time.time()},{"d": 5, "t": time.time()}], restamp=False)

        The optional `restamp` parameter specifies whether or not the database should rewrite the timestamps
        of datapoints which have a timestamp that is less than one that already exists in the database.

        That is, if restamp is False, and a datapoint has a timestamp less than a datapoint that already
        exists in the database, then the insert will fail. If restamp is True, then all datapoints
        with timestamps below the datapoints already in the database will have their timestamps overwritten
        to the same timestamp as the most recent datapoint hat already exists in the database, and the insert will
        succeed.
        """
        if restamp:
            self.db.update(self.path + "/data", datapoint_array)
        else:
            self.db.create(self.path + "/data", datapoint_array)

    def insert(self, data):
        """insert inserts one datapoint with the given data, and appends it to
        the end of the stream::

            s = cdb["mystream"]

            s.create({"type": "string"})

            s.insert("Hello World!")

        """
        self.insert_array([{"d": data}], restamp=True)

    def subscribe(self, callback, transform="", downlink=False):
        """Subscribes to the stream, running the callback function each time datapoints are inserted into
        the given stream. There is an optional transform to the datapoints, and a downlink parameter.::

            s = cdb["mystream"]

            def subscription_callback(stream,data):
                print stream, data

            s.subscribe(subscription_callback)

        The downlink parameter is for downlink streams - it allows to subscribe to the downlink substream,
        before it is acknowledged. This is especially useful for something like lights - have lights be
        a boolean downlink stream, and the light itself be subscribed to the downlink, so that other
        devices can write to the light, turning it on and off::

            def light_control(stream,data):
                light_boolean = data[0]["d"]
                print "Setting light to", light_boolean
                set_light(light_boolean)

                #Acknowledge the write
                return True

            # We don't care about intermediate values, we only want the most recent setting
            # of the light, meaning we want the "if last" transform
            s.subscribe(light_control, downlink=True, transform="if last")

        """
        streampath = self.path
        if downlink:
            streampath += "/downlink"

        return self.db.subscribe(streampath, callback, transform)

    def unsubscribe(self, transform="", downlink=False):
        """Unsubscribes from a previously subscribed stream. Note that the same values of transform
        and downlink must be passed in order to do the correct unsubscribe::

            s.subscribe(callback,transform="if last")
            s.unsubscribe(transform="if last")
        """
        streampath = self.path
        if downlink:
            streampath += "/downlink"

        return self.db.unsubscribe(streampath, transform)

    def __call__(self, t1=None, t2=None, limit=None, i1=None, i2=None, transform=None):
        """By calling the stream as a function, you can query it by either time range or index,
        and further you can perform a custom transform on the stream::

            #Returns all datapoints with their data < 50 from the past minute
            stream(t1=time.time()-60, transform="if $ < 50")

            #Performs an aggregation on the stream, returning a single datapoint
            #which contains the sum of the datapoints
            stream(transform="sum | if last")

        """
        params = query_maker(t1, t2, limit, i1, i2, transform)

        # In order to avoid accidental requests for full streams, ConnectorDB does not permit requests
        # without any url parameters, so we set i1=0 if we are requesting the full stream
        if len(params) == 0:
            params["i1"] = 0

        return self.db.read(self.path + "/data", params).json()

    def __getitem__(self, getrange):
        """Allows accessing the stream just as if it were just one big python array.
        An example::

            #Returns the most recent 5 datapoints from the stream
            stream[-5:]

            #Returns all the data the stream holds.
            stream[:]

        In order to perform transforms on the stream and to aggreagate data, look at __call__,
        which allows getting index ranges along with a transform.
        """
        if not isinstance(getrange, slice):
            # Return the single datapoint
            return self(i1=getrange, i2=getrange + 1)[0]

        # The query is a slice - return the range
        return self(i1=getrange.start, i2=getrange.stop)

    def __len__(self):
        """taking len(stream) returns the number of datapoints saved within the database for the stream"""
        return int(self.db.read(self.path + "/data", {"q": "length"}).text)

    def __repr__(self):
        """Returns a string representation of the stream"""
        return "[Stream:%s]" % (self.path, )

    # -----------------------------------------------------------------------
    # Following are getters and setters of the stream's properties

    @property
    def downlink(self):
        """returns whether the stream is a downlink, meaning that it accepts input (like turning lights on/off)"""
        if "downlink" in self.data:
            return self.data["downlink"]
        return False

    @downlink.setter
    def downlink(self, is_downlink):
        self.set({"downlink": is_downlink})

    @property
    def ephemeral(self):
        """returns whether the stream is ephemeral, meaning that data is not saved, but just passes through the messaging system."""
        if "ephemeral" in self.data:
            return self.data["ephemeral"]
        return False

    @ephemeral.setter
    def ephemeral(self, is_ephemeral):
        """sets whether the stream is ephemeral, meaning that it sets whether the datapoints are saved in the database.
        an ephemeral stream is useful for things which are set very frequently, and which could want a subscription, but
        which are not important enough to be saved in the database"""
        self.set({"ephemeral": is_ephemeral})

    @property
    def schema(self):
        """Returns the JSON schema of the stream as a python dict"""
        if "type" in self.data:
            return json.loads(self.data["type"])
        return None

    @property
    def user(self):
        """user returns the user which owns the given stream"""
        return User(self.db, self.path.split("/")[0])

    @property
    def device(self):
        """returns the device which owns the given stream"""
        splitted_path = self.path.split("/")

        return Device(self.db,
                              splitted_path[0] + "/" + splitted_path[1])


# The import has to go on the bottom because py3 imports are annoying
from ._user import User
from ._device import Device
