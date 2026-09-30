# Selfhosted Setup

My selfhosted homelab setup managed using [Podman](https://podman.io/). All the services are managed using **Podman Compose** as of now. All the services are managed by scripts as of now and most of the management and automation will be migrated with Ansible when I find time.

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
└── ansible
```

# TODO
- [ ] Ansible Playbooks
- [ ] Migrate to Kubernetes
- [ ] Backup using syncthing
