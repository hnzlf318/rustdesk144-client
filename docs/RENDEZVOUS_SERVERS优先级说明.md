# RENDEZVOUS_SERVERS 优先级说明

## 一、RENDEZVOUS_SERVERS 常量定义

**位置**：`libs/hbb_common/src/config.rs:161`

```rust
pub const RENDEZVOUS_SERVERS: &[&str] = &["182.92.140.90"];
```

这是一个**编译时常量**，定义了内置的 ID 服务器地址列表。

---

## 二、优先级顺序（从高到低）

### 在 `Config::get_rendezvous_servers()` 函数中的优先级

**位置**：`libs/hbb_common/src/config.rs:826-850`

```rust
pub fn get_rendezvous_servers() -> Vec<String> {
    // 1. EXE_RENDEZVOUS_SERVER（最高优先级）
    //    来源：Windows License（从 exe 文件名读取）
    let s = EXE_RENDEZVOUS_SERVER.read().unwrap().clone();
    if !s.is_empty() {
        return vec![s];
    }
    
    // 2. Config::get_option("custom-rendezvous-server")
    //    优先级：OVERWRITE_SETTINGS > 用户配置 > DEFAULT_SETTINGS
    let s = Self::get_option("custom-rendezvous-server");
    if !s.is_empty() {
        return vec![s];
    }
    
    // 3. PROD_RENDEZVOUS_SERVER（运行时设置）
    let s = PROD_RENDEZVOUS_SERVER.read().unwrap().clone();
    if !s.is_empty() {
        return vec![s];
    }
    
    // 4. Config::get_option("rendezvous-servers")（如果 serial 过期）
    let serial_obsolute = CONFIG2.read().unwrap().serial > SERIAL;
    if serial_obsolute {
        let ss: Vec<String> = Self::get_option("rendezvous-servers")
            .split(',')
            .filter(|x| x.contains('.'))
            .map(|x| x.to_owned())
            .collect();
        if !ss.is_empty() {
            return ss;
        }
    }
    
    // 5. RENDEZVOUS_SERVERS（最低优先级，兜底值）
    return RENDEZVOUS_SERVERS.iter().map(|x| x.to_string()).collect();
}
```

### 完整优先级列表

| 优先级 | 来源 | 说明 |
|--------|------|------|
| **1（最高）** | `EXE_RENDEZVOUS_SERVER` | Windows License（从 exe 文件名读取） |
| **2** | `Config::get_option("custom-rendezvous-server")` | 用户配置或 `override-settings` / `default-settings` |
| **3** | `PROD_RENDEZVOUS_SERVER` | 运行时设置的服务器 |
| **4** | `Config::get_option("rendezvous-servers")` | 仅在 serial 过期时检查 |
| **5（最低）** | **`RENDEZVOUS_SERVERS`** | **编译时常量，作为兜底值** |

---

## 三、为什么之前读取不到 RENDEZVOUS_SERVERS？

### 问题原因

在心跳包中，之前使用的是 `get_custom_rendezvous_server()` 函数：

```rust
pub fn get_custom_rendezvous_server(custom: String) -> String {
    // 1. Windows License
    // 2. custom 参数
    // 3. PROD_RENDEZVOUS_SERVER
    // ❌ 不检查 RENDEZVOUS_SERVERS
    "".to_owned()  // 如果前面都是空，返回空字符串
}
```

**问题**：`get_custom_rendezvous_server()` **不检查** `RENDEZVOUS_SERVERS`，所以即使 `RENDEZVOUS_SERVERS` 有值，也读取不到。

### 解决方案

修改心跳包代码，使用 `Config::get_rendezvous_servers()` 来获取实际使用的 ID 服务器：

```rust
// 修改前（错误）：
let custom_config = Config::get_option("custom-rendezvous-server");
let id_server = crate::common::get_custom_rendezvous_server(custom_config);
// ❌ 如果前面都是空，返回空字符串，不会读取 RENDEZVOUS_SERVERS

// 修改后（正确）：
let rendezvous_servers = Config::get_rendezvous_servers();
let id_server = rendezvous_servers.first().cloned().unwrap_or_default();
// ✅ 会按照完整优先级检查，包括 RENDEZVOUS_SERVERS
```

---

## 四、使用场景

### 1. 实际连接时

**使用函数**：`Config::get_rendezvous_servers()`

**位置**：
- `src/rendezvous_mediator.rs:103` - 连接服务器时
- `src/client.rs:290` - 建立连接时
- `src/ui_interface.rs:1338-1340` - UI 界面获取服务器列表时

**效果**：会按照完整优先级检查，包括 `RENDEZVOUS_SERVERS`。

### 2. 心跳包上报时（已修复）

**使用函数**：`Config::get_rendezvous_servers()`（修改后）

**位置**：`src/hbbs_http/sync.rs:260`

**效果**：现在会正确读取到实际使用的 ID 服务器，包括 `RENDEZVOUS_SERVERS`。

---

## 五、RENDEZVOUS_SERVERS 的作用

1. **兜底值**：当所有其他配置都为空时，使用 `RENDEZVOUS_SERVERS` 作为默认服务器
2. **编译时内置**：在编译时就确定了默认服务器地址，无需运行时配置
3. **多服务器支持**：`RENDEZVOUS_SERVERS` 是一个数组，可以配置多个服务器地址

---

## 六、修改 RENDEZVOUS_SERVERS 的方法

### 方法一：直接修改源码（编译时内置）

**位置**：`libs/hbb_common/src/config.rs:161`

```rust
pub const RENDEZVOUS_SERVERS: &[&str] = &["182.92.140.90"];  // 修改为你想要的地址
```

**效果**：编译后，如果所有其他配置都为空，会使用这个地址。

### 方法二：通过配置覆盖（运行时）

通过以下方式可以覆盖 `RENDEZVOUS_SERVERS`：

1. **Windows License**（最高优先级）
2. **custom.txt 的 override-settings**（强制生效）
3. **用户在 UI 中设置**（可被 override-settings 覆盖）
4. **custom.txt 的 default-settings**（用户可覆盖）

---

## 七、验证方法

1. **清空所有配置**：
   - 删除 `custom.txt`
   - 清空用户配置中的 `custom-rendezvous-server`
   - 不使用 Windows License

2. **运行客户端**：
   - 客户端应该会使用 `RENDEZVOUS_SERVERS` 的值（`182.92.140.90`）

3. **查看心跳包**：
   - 心跳包中的 `custom-rendezvous-server` 字段应该包含 `182.92.140.90`

---

## 八、总结

- **`RENDEZVOUS_SERVERS` 的优先级是最低的**，作为兜底值使用
- **之前读取不到**是因为心跳包使用了 `get_custom_rendezvous_server()`，它不检查 `RENDEZVOUS_SERVERS`
- **现在已修复**，心跳包使用 `Config::get_rendezvous_servers()`，会正确读取到 `RENDEZVOUS_SERVERS` 的值
- **实际连接时**一直使用的是 `Config::get_rendezvous_servers()`，所以连接功能是正常的

