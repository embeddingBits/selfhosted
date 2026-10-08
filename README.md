# Selfhosted Setup

My selfhosted homelab setup managed using [Podman](https://podman.io/) and **Podman Compose**. Services are managed either with the `scripts/homelab.sh` wrapper or the Ansible playbook in `ansible/`.

# Infrastructure
## Tools 
- [Podman](https://podman.io/): Container orchestration
- [Ansible](https://ansible.com/): Automation and management

## Services

| Service    | Description            | Stack                |
| ---------- | ---------------------- | -------------------- |
| Homepage   | Dashboard              | `docker/homepage`    |
| Flatnotes  | Markdown notes app     | `docker/flatnotes`   |
| Kavita     | Book / reading server  | `docker/kavita`      |
| Navidrome  | Music streaming server | `docker/navidrome`   |

## Directory layout

```
.
├── docker/             # Docker compose files
├── media/
├── scripts/            # Some scripts to manage services
└── ansible/            # Ansible playbook, inventory and vars
```

## Managing with Ansible

The `ansible/` directory contains a playbook that manages the services on this
host. It runs locally (rootless, as the current user) and covers the full
deploy: installing prerequisites, syncing the repo, creating the Flatnotes
`.env`, and starting the stacks.

```sh
cd ansible
ansible-playbook homelab.yml                          # full deploy
ansible-playbook homelab.yml -e homelab_action=up     # start all stacks
ansible-playbook homelab.yml -e homelab_action=down   # stop and remove all stacks
ansible-playbook homelab.yml -e homelab_action=restart
ansible-playbook homelab.yml -e homelab_action=status # show container status
ansible-playbook homelab.yml -e homelab_action=logs   # show recent logs
```

Flatnotes credentials are stored in `ansible/group_vars/all.yml` (placeholders by
default) and are written to `docker/flatnotes/.env` only if that file doesn't
already exist. Set real values there or override with `--extra-vars`.

# TODO
- [x] Ansible Playbooks
- [ ] Migrate to Kubernetes
- [ ] Backup using syncthing
