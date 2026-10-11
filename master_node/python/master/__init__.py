"""Master PC observer / data collector / commander for the tile station.

The line runs without it (the Pi decides and sends finals itself). It connects
to the Pi's MQTT broker to watch every tile, keep its own copy of the data,
show station health, and send the few commands the station accepts.
Contract: documents/programming/station_coordination.md
"""
