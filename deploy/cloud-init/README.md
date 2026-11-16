# Cloud-init

`user-data.yaml` 是节点通用模板，`__HOSTNAME__` 决定角色，映射关系见
`deploy/scripts/install_node.sh`。使用前替换：

- `__HOSTNAME__`、`__SSH_PUBLIC_KEY__`、`__REPO_URL__`
- 数据库/NATS/S3/Redis 地址与凭据
- `__API_TOKEN__`

不要把填好真实凭据的文件提交到 Git。KVM 环境由 `deploy/kvm/provision.sh`
生成 seed；云主机可在控制台粘贴渲染后的 user-data。
