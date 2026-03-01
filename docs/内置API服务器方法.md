# RustDesk 内置 API 服务器方法

## 方法一：通过 custom.txt 的 override-settings（推荐，强制生效）

**优先级最高，会覆盖用户设置，用户无法在 UI 中修改**

### 步骤

1. **准备 custom.txt 文件**（Base64 编码 + 签名）

   custom.txt 的内容应该是 Base64 编码的 JSON，JSON 结构如下：

   ```json
   {
     "app-name": "MyRustDesk",
     "override-settings": {
       "api-server": "http://api.example.com:21118"
     }
   }
   ```

2. **签名和编码**

   - 使用 RustDesk 的私钥对 JSON 进行签名
   - 然后 Base64 编码
   - 最终 custom.txt 里是编码后的字符串

3. **放置位置**

   - Windows: 放在 `rustdesk.exe` 同目录下
   - macOS: 放在 `RustDesk.app/Contents/Resources/` 目录下
   - Linux: 放在可执行文件同目录下

4. **效果**

   - 客户端启动时会自动读取并应用
   - 用户无法在 UI 中修改（会被覆盖）
   - 优先级最高，会覆盖所有其他配置

---

## 方法二：通过 custom.txt 的 default-settings（作为默认值）

**优先级较低，用户可以在 UI 中覆盖**

### 步骤

custom.txt 的 JSON 结构：

```json
{
  "app-name": "MyRustDesk",
  "default-settings": {
    "api-server": "http://api.example.com:21118"
  }
}
```

### 效果

- 如果用户没有在 UI 里设置，就使用这个默认值
- 如果用户在 UI 里设置了，就用用户设置的值（用户设置会覆盖它）

---

## 方法三：通过编译时环境变量（编译时内置）

**在编译时设置，编译后无法修改**

### 步骤

在编译时设置环境变量：

```bash
# Windows PowerShell
$env:API_SERVER="http://api.example.com:21118"
cargo build --release

# Linux/Mac
export API_SERVER="http://api.example.com:21118"
cargo build --release
```

或者在 GitHub Actions 工作流中设置：

```yaml
env:
  API_SERVER: "http://api.example.com:21118"

steps:
  - name: Build
    run: cargo build --release
```

### 优先级

- 低于 Windows License
- 低于用户配置（`Config::get_option("api-server")`）
- 高于从 ID 服务器推导的值
- 高于硬编码默认值

---

## 方法四：通过 Windows License（从 exe 文件名读取）

**Windows 平台专用，优先级最高**

### 步骤

1. **重命名 exe 文件**，格式：`rustdesk-{base64_license}.exe`

2. **License 格式**（Base64 编码的 JSON）：

   ```json
   {
     "host": "id.example.com:21116",
     "api": "http://api.example.com:21118",
     "key": "你的服务器公钥"
   }
   ```

3. **编码和签名**

   - 对 JSON 进行签名
   - Base64 编码
   - 嵌入到 exe 文件名中

### 示例

```
rustdesk-{base64_encoded_signed_json}.exe
```

### 优先级

- **最高优先级**，会覆盖所有其他配置（包括用户设置）

---

## 方法五：通过 custom.txt 顶层直接设置（HARD_SETTINGS）

**不参与 get_option() 优先级，需要手动读取**

### 步骤

custom.txt 的 JSON 结构：

```json
{
  "app-name": "MyRustDesk",
  "api-server": "http://api.example.com:21118"
}
```

### 特点

- 会进入 `HARD_SETTINGS`
- **不会自动参与 `Config::get_option()` 的优先级判断**
- 需要代码中手动读取 `HARD_SETTINGS.read().unwrap().get("api-server")`
- 主要用于特殊场景（如固定密码）

---

## 推荐方案对比

| 方法 | 优先级 | 用户可修改 | 适用场景 |
|------|--------|-----------|---------|
| **override-settings** | 最高 | ❌ 否 | 企业部署，强制使用指定服务器 |
| **default-settings** | 中等 | ✅ 是 | 提供默认值，允许用户修改 |
| **编译时环境变量** | 中等 | ❌ 否 | CI/CD 构建时设置 |
| **Windows License** | 最高 | ❌ 否 | Windows 平台，需要最高优先级 |
| **HARD_SETTINGS（顶层）** | 特殊 | ❌ 不参与优先级 | 特殊场景，需要手动读取 |

---

## 实际运行时优先级（完整顺序）

根据 `get_api_server_()` 函数的实现，实际运行时优先级为：

1. **Windows License**（从 exe 文件名读取）
2. **用户配置的 `api-server`**
   - `override-settings` 里的 `api-server`（最高）
   - 用户在 UI 里设置的 `api-server`（中等）
   - `default-settings` 里的 `api-server`（较低）
3. **编译时环境变量** `API_SERVER`
4. **从 ID 服务器推导**（端口 -2）
5. **硬编码默认值**：`"http://jetion123.com"`

---

## 示例：完整的 custom.txt JSON 结构

```json
{
  "app-name": "MyRustDesk",
  
  "override-settings": {
    "api-server": "http://api.example.com:21118",
    "custom-rendezvous-server": "id.example.com:21116",
    "relay-server": "relay.example.com:21117",
    "key": "你的服务器公钥"
  },
  
  "default-settings": {
    "api-server": "http://backup-api.example.com:21118"
  }
}
```

**说明**：
- `override-settings` 里的值会强制生效（用户无法修改）
- `default-settings` 里的值作为默认值（用户可覆盖）

---

## 验证方法

1. **编译并运行客户端**
2. **查看心跳包**：客户端会通过 `/api/heartbeat` 发送当前使用的 API 服务器地址
3. **检查服务端日志**：看收到的 `api-server` 字段值是否符合预期

---

## 注意事项

1. **custom.txt 需要签名**：必须使用 RustDesk 的私钥签名，否则客户端不会加载
2. **override-settings 会覆盖用户设置**：如果设置了，用户无法在 UI 中修改
3. **编译时环境变量**：只在编译时生效，编译后无法修改
4. **Windows License**：优先级最高，会覆盖所有其他配置

