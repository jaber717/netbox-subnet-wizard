FROM netboxcommunity/netbox:v4.6.9

# Editable install: docker-compose bind-mounts the plugin source over this path,
# so code edits only need a container restart, not a rebuild.
COPY . /opt/plugins/netbox-subnet-wizard
RUN /usr/local/bin/uv pip install -e /opt/plugins/netbox-subnet-wizard
