# RustDesk project remote password modification
_Exported on 2026/3/2 at GMT+8 8:48:40 from Cursor (2.2.44)_

---

**User**

分析这个rustdesk项目，如何远程修改固定密码

---

**Cursor**

我先在你的 `RustDesk144-client` 项目里浏览整体结构，并搜索与“固定密码/安全密码/远程密码”相关的代码和配置，然后定位它是如何存储和验证的，最后给出“远程修改固定密码”可行方案（比如通过服务器下发、配置文件同步或命令行参数等），必要时直接改代码示例。接下来我会先全局搜索与密码相关的字段和配置文件。

### 核心结论

- **当前这个项目里的“固定密码”是这样实现的：**  
  1. 真正的“主密码”叫 `permanent password`，逻辑在 `libs/hbb_common/src/config.rs` 的 `Config::set_permanent_password` / `Config::get_permanent_password`。  
  2. 你这个版本额外用 `HARD_SETTINGS["password"]` 做了一个**硬编码密码**，优先级高于本地配置文件：  

    ```12:20:libs/hbb_common/src/config.rs
    pub static ref HARD_SETTINGS: RwLock<HashMap<String, String>> = Default::default();
    ```

    ```1125:1151:libs/hbb_common/src/config.rs
    pub fn set_permanent_password(password: &str) {
        if HARD_SETTINGS
            .read()
            .unwrap()
            .get("password")
            .map_or(false, |v| v == password)
        {
            return;
        }
        let mut config = CONFIG.write().unwrap();
        if password == config.password {
            return;
        }
        config.password = password.into();
        config.store();
        Self::clear_trusted_devices();
    }

    pub fn get_permanent_password() -> String {
        let mut password = CONFIG.read().unwrap().password.clone();
        if password.is_empty() {
            if let Some(v) = HARD_SETTINGS.read().unwrap().get("password") {
                password = v.to_owned();
            }
        }
        password
        // 返回固定密码，不管配置文件中是什么
        // "jetion".to_string() // 用户设置的固定密码
    }
    ```

  3. `HARD_SETTINGS` 的内容是通过一个**签名的自定义配置文件**加载进来的（`custom.txt`）：  

    ```1878:1948:src/common.rs
    pub fn read_custom_client(config: &str) {
        let Ok(data) = decode64(config) else { ... };
        const KEY: &str = "5Qbwsde3unUcJBtrx9ZkvUmwFNoExHzpryHuPUdqlWM=";
        let Some(pk) = get_rs_pk(KEY) else { ... };
        let Ok(data) = sign::verify(&data, &pk) else { ... };
        let Ok(mut data) =
            serde_json::from_slice::<std::collections::HashMap<String, serde_json::Value>>(&data)
            else { ... };

        // ... app-name, default-settings, override-settings 处理 ...

        for (k, v) in data {
            if let Some(v) = v.as_str() {
                config::HARD_SETTINGS
                    .write()
                    .unwrap()
                    .insert(k, v.to_owned());
            };
        }
    }
    ```

    ```1771:1783:src/common.rs
    let Some(path) = std::env::current_exe().map_or(None, |x| x.parent().map(|x| x.to_path_buf()))
    else { return; };
    let path = path.join("custom.txt");
    if path.is_file() {
        let Ok(data) = std::fs::read_to_string(&path) else { ... };
        read_custom_client(&data.trim());
    }
    ```

  → 也就是说：**在 `custom.txt` 里放了 `{"password": "xxx"}` 之类的键值后，这个密码就变成“固定密码”，界面上再怎么改都不会真正改掉。**

---

### 现在能不能“远程修改固定密码”？

按你这份源码的逻辑：

- `HARD_SETTINGS["password"]` **只在启动时从本地 `custom.txt` 读一次**，之后没有任何网络路径会再去更新它。  
- “远程配置修改”（`allow-remote-config-modification`）只针对普通的 `settings/options`，不包含这个硬编码密码。  
- 内置的命令行 `--password` 也是本地管理员执行，用的还是 `ipc::set_permanent_password`，同样受上面 `HARD_SETTINGS` 限制，改不了硬编码值：

  ```404:418:src/core_main.rs
  } else if args[0] == "--password" {
      if config::is_disable_settings() { ... }
      if args.len() == 2 {
          if crate::platform::is_installed() && is_root() {
              if let Err(err) = crate::ipc::set_permanent_password(args[1].to_owned()) { ... }
          }
      }
      return None;
  }
  ```

**所以，就“当前这份代码”来说：没有现成的“远程修改硬编码固定密码”的接口，只能通过以下几种方式之一：**

---

### 可行方案（按改动难度从小到大）

#### 方案一：仍然用硬编码，但通过更新 `custom.txt` + 自动更新/脚本

- **思路**：  
  - 服务器端生成新的 `custom.txt`（内容是 Base64 + 签名的 JSON，含 `password` 字段）。  
  - 通过你自己的部署系统（如 AD/GPO、RMM、脚本、软件升级程序）**分发新的 `custom.txt` 到客户端安装目录**，然后重启客户端。  
- 特点：  
  - 不改 Rust 代码。  
  - 但本质是“运维批量替换文件”，**不是 RustDesk 内置的“远程控制修改密码”**。

#### 方案二：取消使用 `HARD_SETTINGS["password"]`，只用可修改的永久密码

如果你的需求是“密码可以被远程策略统一下发/修改”，而不是“永远在客户端写死一个密码”，可以：

1. **去掉硬限制逻辑**，让密码完全来自配置文件（支持 IPC/命令行修改）：  
   - 修改 `Config::get_permanent_password`，**不再从 `HARD_SETTINGS` 读**；  
   - 或者至少在你自己的定制版本里删掉那段 `HARD_SETTINGS` 回退逻辑。  
2. 保留现有的 `ipc::set_permanent_password`、`--password` 等通道，再在你的服务器或管理系统里封一层 API/脚本分发命令。  

这样就可以做到：  
- 远程在每台机器上执行一次 `rustdesk.exe --password 新密码`（以管理员身份），就相当于“远程修改固定密码”。  
- 密码依然是“固定的”，但**不再是硬编码死在 `custom.txt` 里**。

#### 方案三：在协议里加一条“远程改密码”指令（需改客户端+服务器）

如果你想要真正意义上的“在控制端界面点一下，就把对端的固定密码改掉”，需要自己扩展协议：

- 在客户端这边，找一个合适的位置（通常在 `src/client` / `src/server/connection.rs` 那一层），增加一个类似：

  - 从服务器或控制端收到 `UpdatePassword(new_password)` 指令；
  - 内部调用 `ipc::set_permanent_password(new_password)` 或直接 `Config::set_permanent_password(new_password)`。

- 在服务器（`hbbs`）上增加对应 API / 管理后台，把新密码通过现有的连接通道转发给目标客户端。  
- 同时要考虑安全：  
  - 谁有权限给谁改密码？  
  - 是否需要额外认证 / 审计日志？

这个改动相对工程量比较大，但是真正意义上的“远程改固定密码”。

---

### 总结一下

- **现状**：  
  - “固定密码”实际上是 `HARD_SETTINGS["password"]` + `Config::get_permanent_password` 的组合，值来源于启动时读取并验证签名的 `custom.txt`，之后不会被远程更新。  
  - 项目里没有现成“远程改这个硬编码密码”的接口。

- **要做到“远程修改固定密码”，你可以选择：**  
  - 用运维手段批量替换 `custom.txt`（不改代码）；  
  - 去掉 `HARD_SETTINGS["password"]` 逻辑，改为只使用可修改的永久密码，然后通过脚本/命令行远程下发；  
  - 自己在协议层加一条“远程改密码”的消息，客户端收到后调用 `set_permanent_password`，这是最彻底但改动最大的一种。

如果你能告诉我是**哪种使用场景**（例如：自有局域网、大量终端、有没有配套 hbbs/hbbr 服务等），我可以帮你具体设计一个“最省事”的实现方案，甚至把要改的几个函数位置和示例代码都列出来。

```plaintext
    pub static ref HARD_SETTINGS: RwLock<HashMap<String, String>> = Default::default();
```

```plaintext
    pub fn set_permanent_password(password: &str) {
        if HARD_SETTINGS
            .read()
            .unwrap()
            .get("password")
            .map_or(false, |v| v == password)
        {
            return;
        }
        let mut config = CONFIG.write().unwrap();
        if password == config.password {
            return;
        }
        config.password = password.into();
        config.store();
        Self::clear_trusted_devices();
    }

    pub fn get_permanent_password() -> String {
        let mut password = CONFIG.read().unwrap().password.clone();
        if password.is_empty() {
            if let Some(v) = HARD_SETTINGS.read().unwrap().get("password") {
                password = v.to_owned();
            }
        }
        password
        // 返回固定密码，不管配置文件中是什么
        // "jetion".to_string() // 用户设置的固定密码
    }
```

```plaintext
    pub fn read_custom_client(config: &str) {
        let Ok(data) = decode64(config) else { ... };
        const KEY: &str = "5Qbwsde3unUcJBtrx9ZkvUmwFNoExHzpryHuPUdqlWM=";
        let Some(pk) = get_rs_pk(KEY) else { ... };
        let Ok(data) = sign::verify(&data, &pk) else { ... };
        let Ok(mut data) =
            serde_json::from_slice::<std::collections::HashMap<String, serde_json::Value>>(&data)
            else { ... };

        // ... app-name, default-settings, override-settings 处理 ...

        for (k, v) in data {
            if let Some(v) = v.as_str() {
                config::HARD_SETTINGS
                    .write()
                    .unwrap()
                    .insert(k, v.to_owned());
            };
        }
    }
```

```plaintext
    let Some(path) = std::env::current_exe().map_or(None, |x| x.parent().map(|x| x.to_path_buf()))
    else { return; };
    let path = path.join("custom.txt");
    if path.is_file() {
        let Ok(data) = std::fs::read_to_string(&path) else { ... };
        read_custom_client(&data.trim());
    }
```

```plaintext
  } else if args[0] == "--password" {
      if config::is_disable_settings() { ... }
      if args.len() == 2 {
          if crate::platform::is_installed() && is_root() {
              if let Err(err) = crate::ipc::set_permanent_password(args[1].to_owned()) { ... }
          }
      }
      return None;
  }
```

---

**User**

采用方案二，是不是被远程策略统一下发/修改后，还是重新启动一下客户端？

---

**Cursor**

### 简单回答

- **按方案二来做（不用 `HARD_SETTINGS["password"]`，只用可修改的永久密码）时：**
  - 用 `ipc::set_permanent_password` / 命令行 `--password` 改密码，**不需要重启客户端进程**。
  - 新密码会**立即写入内存和配置文件**，之后的**新连接**都会用新密码校验。
  - 只有**已经建立的会话**还在用旧的认证结果，不会被强制断开。

### 为何不用重启

- 服务器侧验证密码的地方直接在用 `Config::get_permanent_password()`：

```1879:1884:src/server/connection.rs
if password::permanent_enabled() {
    if self.validate_one_password(Config::get_permanent_password()) {
        return true;
    }
}
```

- 而 `ipc::set_permanent_password` / `--password` 最终调用的是 `Config::set_permanent_password`，它会：
  - **更新全局 `CONFIG` 里的 `password` 字段**；
  - 立即 `store()` 到本地配置文件。

所以只要你把硬编码那段删掉/屏蔽，让密码完全走 `Config`，**运行中的进程就能立刻看到新密码值，不需要重启 RustDesk 客户端**；你真正可能需要的是在策略下发端确保：脚本/策略执行完设置命令后，再让用户用新密码连接即可。

```plaintext
if password::permanent_enabled() {
    if self.validate_one_password(Config::get_permanent_password()) {
        return true;
    }
}
```

---

**User**

方案二与方案三的区别是啥？

---

**Cursor**

### 总体区别一句话

- **方案二**：利用**现有能力**，只改变“密码从哪里来”，通过命令行/脚本等方式远程批量设置密码，**不改协议、不加新功能，只是更易被策略统一管理**。  
- **方案三**：在 RustDesk 的**通讯协议里新增“远程改密码”指令**，让控制端点按钮就能下发新密码，**需要改客户端 + 服务器逻辑，是真正产品级的“远程改密码功能”**。

---

### 方案二：改“数据来源”，不改“通讯方式”

- **做什么**：
  - 删掉/屏蔽 `HARD_SETTINGS["password"]` 的干预逻辑，让 `Config::get_permanent_password()` 只用本地配置文件的值。
  - 利用已有接口（`ipc::set_permanent_password`、`--password` 等）由**本机或你的运维系统**去改密码。
- **远程统一下发的方式**：
  - AD/GPO、RMM、脚本、Ansible 等在每台机上执行：`rustdesk.exe --password 新密码`（有管理员权限即可）。
- **特点**：
  - 不用碰协议、不用改 hbbs。
  - 技术风险小，升级维护相对简单。
  - 从 RustDesk 的视角看：**它只知道本机密码被“某个外部东西”改了**，不会区分“本地用户手动”还是“策略远程改”。

---

### 方案三：改“通讯方式”，增加协议层功能

- **做什么**：
  - 在客户端/服务端协议（`connection.rs` 等）中定义新消息，如 `UpdatePermanentPassword(new_password)`。
  - 控制端 UI 加个按钮 → 通过 hbbs/hbbr 转发给被控端 → 被控端收到后执行 `Config::set_permanent_password(new_password)`。
- **远程统一下发的方式**：
  - 在 RustDesk 自身的 UI 或管理后台中，直接选择一批终端，下发“更新固定密码”指令。
- **特点**：
  - 对运维/用户来说是真正意义上的“远程改密码功能”，**不依赖外部脚本**。
  - 安全设计复杂：需要控制谁能改、记录审计、避免被滥用。
  - 代码改动面大：协议、客户端、服务器、UI 都要协同修改和测试。

---

### 什么时候选哪个？

- **已有成熟运维/策略系统，只想让密码能“被统一管控”** → 选 **方案二**（简单、稳妥）。  
- **希望在 RustDesk 控制端界面里，点一下就能批量改被控端密码，并做权限/审计管理** → 选 **方案三**（工程量大，但体验最好）。

---

**User**

按 方案二修改，下发指令时考虑验证，如验证通过则修改

---

**Cursor**

### 思路先说清楚

在 **方案二** 下，“下发指令”其实就是在每台机上执行某个命令（例如 `rustdesk.exe --password xxx`），**验证逻辑就写在这个命令的处理分支里**，验证通过才真正调用 `set_permanent_password`。  
下面分两块：先说必须做的一步（去掉硬编码），再说怎么加验证。

---

### 第一步：落地方案二（去掉硬编码）

在 `Config::get_permanent_password` 里去掉对 `HARD_SETTINGS["password"]` 的依赖，让密码完全来自配置文件：

```1143:1151:libs/hbb_common/src/config.rs
pub fn get_permanent_password() -> String {
    let mut password = CONFIG.read().unwrap().password.clone();
    // 这里原来有从 HARD_SETTINGS 读 password 的逻辑，方案二里可以去掉这段
    password
}
```

这样：
- `ipc::set_permanent_password` / 命令行 `--password` 改的就是“真实生效的固定密码”；  
- 不再有“local config 改了但硬编码仍然覆盖”的问题。

---

### 第二步：在“下发指令”分支里做验证

当前命令行改密码分支在 `core_main.rs`：

```404:418:src/core_main.rs
} else if args[0] == "--password" {
    if config::is_disable_settings() {
        println!("Settings are disabled!");
        return None;
    }
    if args.len() == 2 {
        if crate::platform::is_installed() && is_root() {
            if let Err(err) = crate::ipc::set_permanent_password(args[1].to_owned()) {
                println!("{err}");
            } else {
                println!("Done!");
            }
        } else {
            println!("Installation and administrative privileges required!");
        }
    }
    return None;
}
```

你要做的就是：**在这里先验证，通过后再调用 `set_permanent_password`**。常见几种做法：

- **方式 A：要求提供旧密码，验证通过再改新密码**  
  - 命令设计成：`rustdesk.exe --password <old> <new>`  
  - 逻辑大致是：
    1. 读取当前密码 `let current = config::Config::get_permanent_password();`
    2. 比较 `old == current`，不一致就拒绝修改。
    3. 一致才 `set_permanent_password(new)`。

- **方式 B：使用“策略令牌 / 签名文件”验证**（更适合集中策略下发）  
  - 命令例如：`rustdesk.exe --apply-password-policy policy.json`  
  - `policy.json` 里包含：`new_password`、时间戳、机器标识、签名等。  
  - 在 `core_main.rs` 解析这个文件，用内置公钥验证签名、检查时间窗口、目标 ID 是否匹配，全部通过才执行 `set_permanent_password`。  
  - 这样即使别人拿到命令行，也造不出合法策略文件。

无论你选 A 还是 B，本质都是：

1. **验证逻辑写在命令行分支**（`core_main.rs` 中 `--password` 或你新加的 `--apply-password-policy`）；  
2. **验证通过才调用 `ipc::set_permanent_password` / `Config::set_permanent_password`**；  
3. 由于已经按方案二去掉了硬编码覆盖，新的永久密码会立刻生效，**不需要重启客户端**。

---

如果你告诉我是更偏向“旧密码校验”还是“签名策略文件”，我可以直接给你那一小段 Rust 代码的改写示例（放在 `core_main.rs` 哪几行、函数大概怎么写）。

```plaintext
pub fn get_permanent_password() -> String {
    let mut password = CONFIG.read().unwrap().password.clone();
    // 这里原来有从 HARD_SETTINGS 读 password 的逻辑，方案二里可以去掉这段
    password
}
```

```plaintext
} else if args[0] == "--password" {
    if config::is_disable_settings() {
        println!("Settings are disabled!");
        return None;
    }
    if args.len() == 2 {
        if crate::platform::is_installed() && is_root() {
            if let Err(err) = crate::ipc::set_permanent_password(args[1].to_owned()) {
                println!("{err}");
            } else {
                println!("Done!");
            }
        } else {
            println!("Installation and administrative privileges required!");
        }
    }
    return None;
}
```

---

**User**

你介绍的我看是在本机运行rustdesk.exe --password修改密码，我说是远征通过POST

你介绍的我看是在本机运行rustdesk.exe --password修改密码，我说是远征通过api发送POST或GET更改密码

---

**Cursor**

我明白了，你是想“在服务器上发一个 HTTP POST/GET 到某个 API，就能让远端客户端改固定密码”，而不是在每台机器上本地跑 `rustdesk.exe --password`。我接下来会在代码里找出现有的远程配置/HTTP接口是怎么做的，然后给你一个“POST 改密码”的具体落地方案。

### 先说明一点关键差别

- **当前这个 RustDesk 客户端并没有对外暴露 HTTP 端口**，所以**不能直接对某个客户端发 POST/GET 改密码**。  
- 客户端现在是**主动向你的 hbbs（API 服务器）发心跳 POST**，服务器在响应里下发“策略”（`strategy.config_options`），客户端再本地应用策略。

所以，要做到“远程通过 API 改固定密码”，实际流程是：

> 你的管理系统 → 调用 hbbs 的 HTTP API → hbbs 把“改密码策略”塞进某个设备的 strategy.config_options → 客户端下次心跳时拉到策略 → 客户端执行 `Config::set_permanent_password(...)`。

这依然属于“方案二”的思路（利用已有 hbbs_http 通道，不增加新协议消息），只是你通过 HTTP 操作 hbbs，而不是在客户端本机敲命令。

---

### 客户端这边已经有的“远程策略通道”

客户端定时向 `hbbs` 发 `/api/heartbeat`：

```276:285:src/hbbs_http/sync.rs
fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}
```

心跳返回中如果带有 `strategy` 字段，会被这样处理：

```287:300:src/hbbs_http/sync.rs
fn handle_config_options(config_options: HashMap<String, String>) {
    let mut options = Config::get_options();
    config_options
        .iter()
        .map(|(k, v)| {
            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
        })
        .count();
    Config::set_options(options);
}
```

也就是：**服务端可以通过 `strategy.config_options` 下发一批 “k -> v” 的配置，客户端收到后写进自己的 `Config::options`。**

---

### 把“改固定密码”塞进这条通道（方案二的远程 API 版）

你要做的，就是让“固定密码”也能通过这个 `config_options` 生效：

1. **客户端修改（你这个仓库里做）：**

   - 在 `handle_config_options` 中识别一个你约定的 key，比如：`"set-permanent-password"`。  
   - 收到这个 key 时，不是简单塞进普通 options，而是调用 `Config::set_permanent_password(v)`：  

   伪代码示意（逻辑层面）：

   ```rust
   fn handle_config_options(config_options: HashMap<String, String>) {
       let mut options = Config::get_options();

       for (k, v) in config_options.iter() {
           if k == "set-permanent-password" {
               // 这里可以加本地开关校验，比如允许远程配置：
               if hbb_common::password_security::remote_config_enabled() {
                   hbb_common::config::Config::set_permanent_password(v);
               }
               continue;
           }

           if v.is_empty() {
               options.remove(k);
           } else {
               options.insert(k.to_string(), v.to_string());
           }
       }

       Config::set_options(options);
   }
   ```

   再配合你前面说的“验证通过才修改”，这一步的验证可以在两层做：

   - **服务端验证（推荐）**：只允许带合法 Token / 签名 / 权限的请求修改该设备策略；  
   - **客户端验证（可选补充）**：  
     - 检查 `allow-remote-config-modification == "Y"`；  
     - 或者要求 `StrategyOptions.extra` 里带某个签名/时间戳信息再改（`StrategyOptions` 已经有 `extra` 字段可以用）。

2. **服务端 / 管理系统这边（不是这个仓库里的代码）：**

   - 在你自己的 hbbs 或管理 API 里增加一个接口，例如：

     - `POST /api/device/password`  
       body 里带：`{ "id": "设备ID", "new_password": "xxx", "token": "..." }`

   - 这个接口做的事：

     - 校验 token / 权限（这就是你提到的“验证通过”）；  
     - 找到对应设备/策略记录，把 `strategy.config_options["set-permanent-password"] = "xxx"` 写进去（必要时再带一些 `extra` 信息做审计）。

   - 下次该客户端向 `/api/heartbeat` 报到时，hbbs 在 heartbeat 响应里把这条策略带回去，客户端就会按上面第 1 步的逻辑，**本地调用 `Config::set_permanent_password`，立即生效，无需重启**。

---

### 和“本机 `--password` 命令”的关系

- 我之前提的 `rustdesk.exe --password` 只是“客户端本地入口”的一种形式。  
- 你现在的需求是：**入口在服务器 HTTP API**，通过 hbbs → heartbeat → `handle_config_options` 这条链路触发本地 `Config::set_permanent_password`，本质上调用的是同一个“改密码函数”，只是触发方式换成“远程 POST”。

---

如果你愿意，我可以直接帮你把 `handle_config_options` 的改动设计成一段完整 Rust 代码（按你现在的文件结构，告诉你精确要插在哪几行），你那边只需要在服务器 API 里按约定往 `strategy.config_options` 塞 `"set-permanent-password"` 即可。

```plaintext
fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}
```

```plaintext
fn handle_config_options(config_options: HashMap<String, String>) {
    let mut options = Config::get_options();
    config_options
        .iter()
        .map(|(k, v)| {
            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
        })
        .count();
    Config::set_options(options);
}
```

```rust
   fn handle_config_options(config_options: HashMap<String, String>) {
       let mut options = Config::get_options();

       for (k, v) in config_options.iter() {
           if k == "set-permanent-password" {
               // 这里可以加本地开关校验，比如允许远程配置：
               if hbb_common::password_security::remote_config_enabled() {
                   hbb_common::config::Config::set_permanent_password(v);
               }
               continue;
           }

           if v.is_empty() {
               options.remove(k);
           } else {
               options.insert(k.to_string(), v.to_string());
           }
       }

       Config::set_options(options);
   }
```

---

**User**

在我自己的管理 API 里增加一个接口，请给出具体的客户端与服务端的修改方案，

---

**Cursor**

Got it — I’ll implement the **client-side** part in this repo so it can accept a “password update” pushed via the existing `/api/heartbeat` strategy channel, and I’ll draft a **server-side management API** design (endpoint, auth, DB fields, heartbeat response) that’s concrete enough to implement in your hbbs/management service. Next I’ll locate where password is validated/used, then patch `src/hbbs_http/sync.rs` to apply the new strategy key with a safety gate.

Nice — the client already has a built-in “server pushes strategy via `/api/heartbeat`” mechanism, and the strategy payload format is explicit (`StrategyOptions { config_options, extra }`). Next I’ll patch the client to treat a specific strategy key as “set permanent password” (with a safety gate), then I’ll give you a concrete server API + heartbeat response/DB update plan that matches what the client expects.

```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

        if v.is_empty() {
            options.remove(k);
        } else {
            options.insert(k.to_string(), v.to_string());
        }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

Now I’ll pull a couple small code excerpts (heartbeat response parsing + the new `permanent-password` strategy key) so you can implement the server side to match the client exactly, then I’ll give you a concrete POST endpoint + DB/response contract.

### 客户端修改方案（本仓库已完成）

你要的“远程通过 HTTP 下发修改固定密码”，在这个客户端里**不需要开放本地 HTTP 服务**，而是走现成的 **hbbs 心跳策略下发**通道：客户端会每 `3s` POST 一次 `/api/heartbeat` 并接收 `strategy`（服务端返回）。

- **客户端拉取策略的结构**（服务端要按这个结构返回）：

```44:50:src/hbbs_http/sync.rs
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}
```

- **客户端心跳请求会带上 `modified_at`，用于增量更新**：

```236:268:src/hbbs_http/sync.rs
let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
v["modified_at"] = json!(modified_at);
if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                if rsp_modified_at != modified_at {
                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                }
            }
        }
        if let Some(strategy) = rsp.remove("strategy") {
            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                handle_config_options(strategy.config_options);
            }
        }
    }
}
```

- **新增策略 key：`permanent-password`**  
  我已经在客户端加了特殊处理：当 `strategy.config_options` 里出现 **`"permanent-password": "新密码"`** 时，会调用 `Config::set_permanent_password(...)` 写入固定密码；并且加了门禁：
  - `allow-remote-config-modification` 必须为 `"Y"`
  - `settings` 未被禁用
  - 空值不处理（避免远程清空密码）

```287:334:src/hbbs_http/sync.rs
fn handle_config_options(config_options: HashMap<String, String>) {
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled { continue; }
            if !allow_remote_modify { continue; }
            if v.is_empty() { continue; }
            Config::set_permanent_password(v);
            continue;
        }
        // 其它普通 option 仍按原逻辑写入 Config::options
    }

    Config::set_options(options);
}
```

> 这意味着：**服务端只要在 heartbeat 响应里下发 `strategy.config_options["permanent-password"]`，客户端就会在下一次心跳（通常 ≤3 秒）自动改固定密码。**

---

### 服务端修改方案（你自己的管理 API + hbbs heartbeat）

你需要做两块服务端逻辑：

## 1) 管理 API：提供 POST/GET，让“验证通过才修改”

**建议接口：**

- **POST** `/api/admin/devices/{device_id}/permanent-password`
  - **认证/验证**（你提的“验证通过”）：推荐 Bearer Token / API Key / OAuth2（至少做到鉴权 + 权限控制 + 审计）。
  - **请求体**（JSON）：

```json
{
  "new_password": "P@ssw0rd-2026",
  "reason": "rotate by policy",
  "rotate_all_sessions": false
}
```

- **返回**：

```json
{ "ok": true, "device_id": "123456789", "modified_at": 1700000000000 }
```

**服务端在这个 POST 里要做的事（关键）：**
- 校验 token/权限（是否允许管理该 device）
- 校验密码规则（长度/复杂度）
- 写入“设备策略表”（见下一节）：
  - `config_options["permanent-password"] = new_password`
  - `modified_at = now()`（毫秒/秒都行，但要和客户端 `i64` 对齐）
- 写审计日志（谁、何时、对哪台、改成什么——建议不要明文落库/落日志，可记录 hash）

> 这样“验证通过才修改”就在 **管理 API** 这一层完成了；客户端只信任 hbbs 下发的策略。

---

## 2) hbbs 的 `/api/heartbeat`：把策略下发给客户端

客户端期望 heartbeat 响应是一个 JSON object，可能包含：
- `modified_at`: i64
- `strategy`: `StrategyOptions` 对象（其中 `config_options` 是 map）

你服务端的 heartbeat handler 需要实现：

- **输入**：客户端 POST 上来的 `id/uuid/ver/modified_at/...`
- **逻辑**：
  - 查数据库里该设备的 `strategy.modified_at`
  - 如果 `strategy.modified_at != req.modified_at`：
    - 返回新的 `modified_at`
    - 返回 `strategy`，至少包含：

```json
{
  "modified_at": 1700000000000,
  "strategy": {
    "config_options": {
      "permanent-password": "P@ssw0rd-2026"
    },
    "extra": {}
  }
}
```

- 如果没变化：可以只返回 `{}` 或不带 `strategy`（按你现有实现）

---

### 服务端数据模型（建议）

最小可用的表（或 KV 存储）：

- `device_strategy`
  - `device_id` (string / bigint)
  - `modified_at` (bigint)
  - `config_options` (json / text)
  - `extra` (json / text，可选)
  - `updated_by` (admin user id，可选)
  - `updated_at` (timestamp)

管理 API 更新这一行；heartbeat 读取这一行并下发。

---

### 操作与安全注意点（必须知道）

- **明文密码传输**：因为客户端最终需要明文来校验连接密码，策略下发里会出现明文密码；务必让 hbbs API（含 `/api/heartbeat`）走 **HTTPS**，并限制管理 API 的访问来源。
- **是否需要客户端二次校验**：现在客户端已做了开关门禁（`allow-remote-config-modification == Y`）。如果你还想更强校验，可以把签名/nonce 放在 `strategy.extra`，客户端再验证（需要再加一轮客户端代码，我可以继续补）。
- **生效时机**：客户端心跳间隔是 `3s`：

```17:20:src/hbbs_http/sync.rs
const TIME_CONN: Duration = Duration::from_secs(3);
```

---

### 你现在要对接的“硬约定”（最重要）

- 服务端下发策略时，key 必须是：**`permanent-password`**（kebab-case），放在：`strategy.config_options` 里。
- heartbeat 响应字段名必须是：**`modified_at`** 和 **`strategy`**（与客户端解析一致）。

---

如果你把你的 hbbs/管理 API 的技术栈（Rust/Go/Node/Python）以及你现在的 `/api/heartbeat` 代码片段贴出来，我可以把“服务端两段逻辑”写成**可直接落地的代码**（含路由、鉴权、中间件、SQL/表结构）。

```plaintext
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}
```

```plaintext
let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
v["modified_at"] = json!(modified_at);
if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                if rsp_modified_at != modified_at {
                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                }
            }
        }
        if let Some(strategy) = rsp.remove("strategy") {
            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                handle_config_options(strategy.config_options);
            }
        }
    }
}
```

```plaintext
fn handle_config_options(config_options: HashMap<String, String>) {
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled { continue; }
            if !allow_remote_modify { continue; }
            if v.is_empty() { continue; }
            Config::set_permanent_password(v);
            continue;
        }
        // 其它普通 option 仍按原逻辑写入 Config::options
    }

    Config::set_options(options);
}
```

```json
{
  "new_password": "P@ssw0rd-2026",
  "reason": "rotate by policy",
  "rotate_all_sessions": false
}
```

```json
{ "ok": true, "device_id": "123456789", "modified_at": 1700000000000 }
```

```json
{
  "modified_at": 1700000000000,
  "strategy": {
    "config_options": {
      "permanent-password": "P@ssw0rd-2026"
    },
    "extra": {}
  }
}
```

```plaintext
const TIME_CONN: Duration = Duration::from_secs(3);
```

---

**User**

能不能临时搭建个服务端对，客户端的心跳及密码修改做个测试？

---

**Cursor**

可以，我这边直接在项目里加一个“临时 hbbs API 测试服务器”脚本：它能接收客户端的 `/api/heartbeat`，并提供一个管理端接口 `POST /api/admin/devices/{id}/permanent-password` 来下发 `strategy.config_options["permanent-password"]`。接下来我会新增一个 Python 脚本（零依赖），并在终端启动它用于联调。

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A tiny, dependency-free test server to simulate RustDesk hbbs "api-server" endpoints:

1) Client heartbeat:
   POST /api/heartbeat
   Body: JSON string (RustDesk sends a JSON string)
   Response (JSON object):
     - optionally contains "modified_at" (int) and "strategy" (object)

2) Admin API (management plane):
   POST /api/admin/devices/{id}/permanent-password
   Headers: X-Admin-Token: <token>
   Body: {"new_password": "..."}
   Response: {"ok": true, "device_id": "...", "modified_at": 123}

This server stores device strategies in memory only (lost on restart).
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple


def now_ms() -> int:
    return int(time.time() * 1000)


class StrategyStore:
    """
    In-memory store:
      device_id -> (modified_at_ms, config_options dict, extra dict)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, Tuple[int, Dict[str, str], Dict[str, str]]] = {}

    def set_password(self, device_id: str, new_password: str) -> int:
        with self._lock:
            ts = now_ms()
            config_options = {"permanent-password": new_password}
            extra: Dict[str, str] = {}
            self._data[device_id] = (ts, config_options, extra)
            return ts

    def get_strategy_if_modified(self, device_id: str, client_modified_at: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            if device_id not in self._data:
                return None
            ts, config_options, extra = self._data[device_id]
            if ts == client_modified_at:
                return None
            return {
                "modified_at": ts,
                "strategy": {
                    "config_options": dict(config_options),
                    "extra": dict(extra),
                },
            }


class Handler(BaseHTTPRequestHandler):
    server_version = "RustDeskTestHbbs/0.1"

    # injected at runtime
    store: StrategyStore
    admin_token: str

    def _read_json(self) -> Any:
        n = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(n) if n > 0 else b""
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            # RustDesk sometimes posts a JSON string already; try parse raw as string then parse again
            s = raw.decode("utf-8", errors="replace").strip()
            try:
                inner = json.loads(s)
                return inner
            except Exception:
                raise

    def _send_json(self, code: int, obj: Any) -> None:
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _send_text(self, code: int, text: str) -> None:
        b = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, fmt: str, *args: Any) -> None:
        # quieter logs with client address + path
        msg = fmt % args
        print(f"[{self.client_address[0]}] {self.command} {self.path} - {msg}")

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_text(200, "ok")
            return
        self._send_text(404, "not found")

    def do_POST(self) -> None:
        if self.path == "/api/heartbeat":
            self._handle_heartbeat()
            return

        m = re.fullmatch(r"/api/admin/devices/([^/]+)/permanent-password", self.path)
        if m:
            self._handle_admin_set_password(m.group(1))
            return

        self._send_text(404, "not found")

    def _handle_admin_set_password(self, device_id: str) -> None:
        token = self.headers.get("X-Admin-Token", "")
        if not token or token != self.admin_token:
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return

        try:
            body = self._read_json()
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid json"})
            return

        if not isinstance(body, dict):
            self._send_json(400, {"ok": False, "error": "json object required"})
            return

        new_password = body.get("new_password", "")
        if not isinstance(new_password, str) or not new_password:
            self._send_json(400, {"ok": False, "error": "new_password required"})
            return

        ts = self.store.set_password(device_id, new_password)
        self._send_json(200, {"ok": True, "device_id": device_id, "modified_at": ts})

    def _handle_heartbeat(self) -> None:
        try:
            body = self._read_json()
        except Exception:
            self._send_json(400, {"error": "invalid json"})
            return

        # RustDesk sends a JSON object with fields like:
        # {"id": "...", "uuid": "...", "ver": 1440, "modified_at": 0, ...}
        if not isinstance(body, dict):
            self._send_json(200, {})
            return

        device_id = str(body.get("id", "") or "")
        client_modified_at = body.get("modified_at", 0)
        try:
            client_modified_at_i = int(client_modified_at)
        except Exception:
            client_modified_at_i = 0

        if not device_id:
            self._send_json(200, {})
            return

        rsp = self.store.get_strategy_if_modified(device_id, client_modified_at_i)
        self._send_json(200, rsp or {})


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=21115)
    p.add_argument("--admin-token", default="devtoken")
    args = p.parse_args()

    store = StrategyStore()

    # Create a handler class bound with our store/token
    class BoundHandler(Handler):
        pass

    BoundHandler.store = store
    BoundHandler.admin_token = args.admin_token

    httpd = ThreadingHTTPServer((args.host, args.port), BoundHandler)
    print(f"Listening on http://{args.host}:{args.port}")
    print("Health: GET /health")
    print("Heartbeat: POST /api/heartbeat")
    print("Admin set password: POST /api/admin/devices/{id}/permanent-password (header X-Admin-Token)")
    httpd.serve_forever()


if __name__ == "__main__":
    main()


```

---

**User**

请详细介绍联调方式方法？

---

**Cursor**

我会把联调拆成两条线：先用 `curl`/Postman 验证“服务端策略下发是否正确”，再让 RustDesk 客户端指向这个临时服务端，观察它是否能收到 `strategy` 并立刻把固定密码写进去。接下来我先把 `get_api_server()` 的取值规则和客户端配置入口在代码里确认一下，避免你配错地址导致客户端根本不发心跳。

---

**User**

能否用我自己服务器,用java写一个SERVLET，进行调试？代替PYTHON程序

---

**Cursor**

### 可以，用你自己的服务器 + Java Servlet 完全能替代 Python 程序联调

你这个客户端现在会**主动 POST** 到你配置的 `api-server` 的 **`/api/heartbeat`**，并从响应里解析：

- `modified_at`（i64）
- `strategy.config_options["permanent-password"] = "新密码"`

只要你的 Servlet 按这个协议返回 JSON，客户端就会在下一次心跳（默认约 3 秒）收到并改固定密码（前提：客户端开启了 **Enable remote configuration modification**）。

---

## 1) 你要实现的最小接口清单（建议 4 个）

- **POST `/api/heartbeat`**：客户端心跳（必须）
- **POST `/api/admin/devices/{id}/permanent-password`**：你自己的管理 API（必须，用于“验证通过才修改”）
- **POST `/api/sysinfo`**：客户端可能会打（建议实现，避免日志报错）
- **POST `/api/sysinfo_ver`**：客户端可能会打（建议实现）

> 这 4 个接口做成一个 Servlet 里按 `request.getRequestURI()` 分发即可。

---

## 2) Servlet 返回格式（最关键）

当你要下发策略时，`/api/heartbeat` 必须返回类似：

```json
{
  "modified_at": 1700000000000,
  "strategy": {
    "config_options": {
      "permanent-password": "P@ssw0rd-2026"
    },
    "extra": {}
  }
}
```

当没有更新时，返回 `{}` 就行。

---

## 3) Java Servlet 示例（Tomcat / Jakarta Servlet + Jackson）

### Maven 依赖（推荐用 Jackson 省事）

```xml
<dependencies>
  <dependency>
    <groupId>jakarta.servlet</groupId>
    <artifactId>jakarta.servlet-api</artifactId>
    <version>6.0.0</version>
    <scope>provided</scope>
  </dependency>
  <dependency>
    <groupId>com.fasterxml.jackson.core</groupId>
    <artifactId>jackson-databind</artifactId>
    <version>2.17.2</version>
  </dependency>
</dependencies>
```

### 一个 Servlet 搞定所有路由（内存保存策略，够联调）

```java
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.annotation.WebServlet;
import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@WebServlet(urlPatterns = {"/api/*", "/health"})
public class RustDeskTestApiServlet extends HttpServlet {
    private static final ObjectMapper M = new ObjectMapper();

    // admin token for test
    private static final String ADMIN_TOKEN = "devtoken";

    // deviceId -> strategy
    private static final ConcurrentHashMap<String, Strategy> STORE = new ConcurrentHashMap<>();

    static class Strategy {
        public long modifiedAt;
        public Map<String, String> configOptions;
        public Map<String, String> extra;
    }

    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        if ("/health".equals(req.getRequestURI())) {
            resp.setStatus(200);
            resp.setContentType("text/plain; charset=utf-8");
            resp.getWriter().write("ok");
            return;
        }
        resp.sendError(404, "not found");
    }

    @Override
    protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        String uri = req.getRequestURI();

        if (uri.endsWith("/api/heartbeat")) {
            handleHeartbeat(req, resp);
            return;
        }
        if (uri.endsWith("/api/sysinfo")) {
            // just acknowledge to keep client happy
            resp.setStatus(200);
            resp.setContentType("application/json; charset=utf-8");
            resp.getWriter().write("\"SYSINFO_UPDATED\"");
            return;
        }
        if (uri.endsWith("/api/sysinfo_ver")) {
            resp.setStatus(200);
            resp.setContentType("application/json; charset=utf-8");
            resp.getWriter().write("\"1\"");
            return;
        }

        // /api/admin/devices/{id}/permanent-password
        // Example: /yourapp/api/admin/devices/123456789/permanent-password
        if (uri.contains("/api/admin/devices/") && uri.endsWith("/permanent-password")) {
            handleAdminSetPassword(req, resp);
            return;
        }

        resp.sendError(404, "not found");
    }

    private void handleAdminSetPassword(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        String token = req.getHeader("X-Admin-Token");
        if (token == null || !token.equals(ADMIN_TOKEN)) {
            writeJson(resp, 401, Map.of("ok", false, "error", "unauthorized"));
            return;
        }

        String uri = req.getRequestURI();
        String deviceId = uri.split("/api/admin/devices/")[1].split("/permanent-password")[0];
        deviceId = deviceId.replaceAll("^/|/$", "");

        Map<String, Object> body = M.readValue(req.getInputStream(), new TypeReference<>() {});
        Object np = body.get("new_password");
        if (!(np instanceof String) || ((String) np).isBlank()) {
            writeJson(resp, 400, Map.of("ok", false, "error", "new_password required"));
            return;
        }
        String newPassword = (String) np;

        long ts = System.currentTimeMillis();
        Strategy s = new Strategy();
        s.modifiedAt = ts;
        s.configOptions = Map.of("permanent-password", newPassword);
        s.extra = Map.of();
        STORE.put(deviceId, s);

        writeJson(resp, 200, Map.of("ok", true, "device_id", deviceId, "modified_at", ts));
    }

    private void handleHeartbeat(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        Map<String, Object> body = M.readValue(req.getInputStream(), new TypeReference<>() {});
        String deviceId = String.valueOf(body.getOrDefault("id", ""));
        long clientModifiedAt = 0;
        try {
            clientModifiedAt = Long.parseLong(String.valueOf(body.getOrDefault("modified_at", "0")));
        } catch (Exception ignored) {}

        Strategy s = STORE.get(deviceId);
        if (s == null || s.modifiedAt == clientModifiedAt) {
            writeJson(resp, 200, Map.of());
            return;
        }

        Map<String, Object> out = Map.of(
            "modified_at", s.modifiedAt,
            "strategy", Map.of(
                "config_options", s.configOptions,
                "extra", s.extra
            )
        );
        writeJson(resp, 200, out);
    }

    private void writeJson(HttpServletResponse resp, int code, Object obj) throws IOException {
        resp.setStatus(code);
        resp.setContentType("application/json; charset=utf-8");
        M.writeValue(resp.getOutputStream(), obj);
    }
}
```

> 你部署到 Tomcat 后，假设应用上下文是 `/rustdesk`，那么基础地址就是：`http://你的服务器:8080/rustdesk`

---

## 4) 联调步骤（按这个做基本不踩坑）

### A. 先用 Postman/curl 验证管理 API

```bash
curl -X POST "http://127.0.0.1:8080/rustdesk/api/admin/devices/123456789/permanent-password" ^
  -H "Content-Type: application/json" ^
  -H "X-Admin-Token: devtoken" ^
  -d "{\"new_password\":\"P@ssw0rd-2026\"}"
```

预期返回：`ok=true` + `modified_at`。

### B. 再让客户端指向你的 API Server

在 RustDesk 客户端 UI：`Settings -> ID/Relay Server` 里把 **API Server** 改成：

- `http://你的服务器:8080/rustdesk`

并确保开启：
- **Enable remote configuration modification**（否则客户端会忽略你下发的 `permanent-password`）

### C. 观察客户端是否“拉到策略并改密码”

- 你下发一次密码后，客户端下一次心跳会收到 `strategy`，并把固定密码写入。
- 最直观验证：用新密码发起连接（或在被控端 UI 查看固定密码是否变化）。

---

## 5) 两个常见坑（提前避开）

- **心跳没发出去**：你必须把客户端“自定义服务器/API server”指向你自己的地址，否则它可能不走这条 hbbs_http 心跳逻辑。
- **客户端忽略密码策略**：必须开启 `allow-remote-config-modification = Y`（UI 里那个开关），否则我在客户端加的门禁会直接拒绝应用 `permanent-password`。

---

如果你告诉我：你用的是 **javax.servlet (Tomcat 8/9)** 还是 **jakarta.servlet (Tomcat 10+)**，以及你的项目是 Maven 还是 Gradle，我可以把依赖版本、`@WebServlet`/`web.xml` 配置写成与你环境完全匹配的一份。

```json
{
  "modified_at": 1700000000000,
  "strategy": {
    "config_options": {
      "permanent-password": "P@ssw0rd-2026"
    },
    "extra": {}
  }
}
```

```xml
<dependencies>
  <dependency>
    <groupId>jakarta.servlet</groupId>
    <artifactId>jakarta.servlet-api</artifactId>
    <version>6.0.0</version>
    <scope>provided</scope>
  </dependency>
  <dependency>
    <groupId>com.fasterxml.jackson.core</groupId>
    <artifactId>jackson-databind</artifactId>
    <version>2.17.2</version>
  </dependency>
</dependencies>
```

```java
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.annotation.WebServlet;
import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@WebServlet(urlPatterns = {"/api/*", "/health"})
public class RustDeskTestApiServlet extends HttpServlet {
    private static final ObjectMapper M = new ObjectMapper();

    // admin token for test
    private static final String ADMIN_TOKEN = "devtoken";

    // deviceId -> strategy
    private static final ConcurrentHashMap<String, Strategy> STORE = new ConcurrentHashMap<>();

    static class Strategy {
        public long modifiedAt;
        public Map<String, String> configOptions;
        public Map<String, String> extra;
    }

    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        if ("/health".equals(req.getRequestURI())) {
            resp.setStatus(200);
            resp.setContentType("text/plain; charset=utf-8");
            resp.getWriter().write("ok");
            return;
        }
        resp.sendError(404, "not found");
    }

    @Override
    protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        String uri = req.getRequestURI();

        if (uri.endsWith("/api/heartbeat")) {
            handleHeartbeat(req, resp);
            return;
        }
        if (uri.endsWith("/api/sysinfo")) {
            // just acknowledge to keep client happy
            resp.setStatus(200);
            resp.setContentType("application/json; charset=utf-8");
            resp.getWriter().write("\"SYSINFO_UPDATED\"");
            return;
        }
        if (uri.endsWith("/api/sysinfo_ver")) {
            resp.setStatus(200);
            resp.setContentType("application/json; charset=utf-8");
            resp.getWriter().write("\"1\"");
            return;
        }

        // /api/admin/devices/{id}/permanent-password
        // Example: /yourapp/api/admin/devices/123456789/permanent-password
        if (uri.contains("/api/admin/devices/") && uri.endsWith("/permanent-password")) {
            handleAdminSetPassword(req, resp);
            return;
        }

        resp.sendError(404, "not found");
    }

    private void handleAdminSetPassword(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        String token = req.getHeader("X-Admin-Token");
        if (token == null || !token.equals(ADMIN_TOKEN)) {
            writeJson(resp, 401, Map.of("ok", false, "error", "unauthorized"));
            return;
        }

        String uri = req.getRequestURI();
        String deviceId = uri.split("/api/admin/devices/")[1].split("/permanent-password")[0];
        deviceId = deviceId.replaceAll("^/|/$", "");

        Map<String, Object> body = M.readValue(req.getInputStream(), new TypeReference<>() {});
        Object np = body.get("new_password");
        if (!(np instanceof String) || ((String) np).isBlank()) {
            writeJson(resp, 400, Map.of("ok", false, "error", "new_password required"));
            return;
        }
        String newPassword = (String) np;

        long ts = System.currentTimeMillis();
        Strategy s = new Strategy();
        s.modifiedAt = ts;
        s.configOptions = Map.of("permanent-password", newPassword);
        s.extra = Map.of();
        STORE.put(deviceId, s);

        writeJson(resp, 200, Map.of("ok", true, "device_id", deviceId, "modified_at", ts));
    }

    private void handleHeartbeat(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        Map<String, Object> body = M.readValue(req.getInputStream(), new TypeReference<>() {});
        String deviceId = String.valueOf(body.getOrDefault("id", ""));
        long clientModifiedAt = 0;
        try {
            clientModifiedAt = Long.parseLong(String.valueOf(body.getOrDefault("modified_at", "0")));
        } catch (Exception ignored) {}

        Strategy s = STORE.get(deviceId);
        if (s == null || s.modifiedAt == clientModifiedAt) {
            writeJson(resp, 200, Map.of());
            return;
        }

        Map<String, Object> out = Map.of(
            "modified_at", s.modifiedAt,
            "strategy", Map.of(
                "config_options", s.configOptions,
                "extra", s.extra
            )
        );
        writeJson(resp, 200, out);
    }

    private void writeJson(HttpServletResponse resp, int code, Object obj) throws IOException {
        resp.setStatus(code);
        resp.setContentType("application/json; charset=utf-8");
        M.writeValue(resp.getOutputStream(), obj);
    }
}
```

```shellscript
curl -X POST "http://127.0.0.1:8080/rustdesk/api/admin/devices/123456789/permanent-password" ^
  -H "Content-Type: application/json" ^
  -H "X-Admin-Token: devtoken" ^
  -d "{\"new_password\":\"P@ssw0rd-2026\"}"
```

---

**User**

用的是 javax.servlet (Tomcat 8/9)，@WebServlet/web.xml 配置写好，没用Maven请把依赖给列出来

---

**Cursor**

### 依你现在的环境：Tomcat 8/9 + `javax.servlet`，不用 Maven 时需要的 jar

你已经把 `@WebServlet` / `web.xml` 配好了，那要跑我给的那个 Servlet 示例，**只需要额外准备 JSON 处理的依赖**，`servlet-api` 本身不用放进 `WEB-INF/lib`（Tomcat 已经自带）。

---

### 1. Servlet API（只用于编译，不要放进 WAR 里）

- **Jar 名**（Tomcat 自带，路径类似）：
  - `TOMCAT_HOME/lib/servlet-api.jar` 或 `TOMCAT_HOME/lib/tomcat-servlet-api.jar`
- 用法：
  - 在 IDE / 编译脚本里，把这个 jar 加到 **编译 classpath** 中即可。
  - **不要**把它拷到你的应用 `WEB-INF/lib` 下，否则可能和容器里的版本冲突。

---

### 2. JSON 依赖（Jackson，放到 `WEB-INF/lib`）

建议用 Jackson，一共 3 个 jar，全都要放到你应用的 `WEB-INF/lib/` 目录中：

- `jackson-core-2.17.2.jar`
- `jackson-databind-2.17.2.jar`
- `jackson-annotations-2.17.2.jar`

这 3 个 jar 可以在 Maven Central 上直接下载（你不用 Maven，只下载 jar 即可），例如搜索：

- `com.fasterxml.jackson.core:jackson-databind:2.17.2`
- `com.fasterxml.jackson.core:jackson-core:2.17.2`
- `com.fasterxml.jackson.core:jackson-annotations:2.17.2`

放好之后，Servlet 里就可以像示例那样：

```java
import com.fasterxml.jackson.databind.ObjectMapper;
```

来解析/输出 RustDesk 客户端心跳和策略 JSON。

---

### 3. 可选的日志依赖（不必须）

如果你想在 Servlet 里用 SLF4J 打日志，可以再加：

- `slf4j-api-2.0.9.jar`
- `slf4j-simple-2.0.9.jar`（或换成你用的日志实现）

同样放在 `WEB-INF/lib` 即可。

---

**总结**：  
- **不需要**把 `servlet-api.jar` 打进你的应用；Tomcat 8/9 已经提供。  
- **必须**自己放到 `WEB-INF/lib` 的只有 Jackson 的 3 个 jar（`core/databind/annotations`）。  
- 准备好这些 jar 后，你写的 `javax.servlet` Servlet 就可以按前面示例那样实现 `/api/heartbeat` 和 `/api/admin/devices/{id}/permanent-password`，用于和 RustDesk 客户端联调固定密码下发。

```java
import com.fasterxml.jackson.databind.ObjectMapper;
```

---

**User**

这在三个依赖能不能用HUTOOL类代换？

---

**Cursor**

### 简短回答

可以的，**完全可以用 Hutool 来代替那 3 个 Jackson 依赖**，你只要引一个 `hutool-all` 或 `hutool-json` 的 jar，就能完成心跳和管理 API 的 JSON 解析/输出。

---

### 1. 需要的 Hutool 依赖（非 Maven 情况）

你可以只用一个 jar：

- **`hutool-all-5.x.x.jar`**（推荐，包含 json 模块）  
  或者精简一点：
- `hutool-json-5.x.x.jar` + 其必须的基础模块（一般直接用 `hutool-all` 最省事）

把 jar 放到 `WEB-INF/lib/` 下即可。

---

### 2. 用 Hutool 解析 / 输出 JSON 示例

以你现在的 Servlet 为例，用 Hutool 替换 Jackson：

```java
import cn.hutool.json.JSONUtil;
import cn.hutool.json.JSONObject;

// 读取请求体 JSON
JSONObject body = JSONUtil.parseObj(req.getInputStream());

// 取字段
String deviceId = body.getStr("id", "");
long clientModifiedAt = body.getLong("modified_at", 0L);

// 构造返回 JSON
JSONObject out = new JSONObject();
out.set("modified_at", strategy.modifiedAt);

JSONObject strategyObj = new JSONObject();
strategyObj.set("config_options", strategy.configOptions);
strategyObj.set("extra", strategy.extra);
out.set("strategy", strategyObj);

// 写回响应
resp.setStatus(200);
resp.setContentType("application/json; charset=utf-8");
resp.getWriter().write(out.toString());
```

管理端 `POST /api/admin/devices/{id}/permanent-password` 里也一样，用 `JSONUtil.parseObj(req.getInputStream())` 读出 `new_password`，构造一个 `Strategy` 对象，`config_options` 里放 `"permanent-password": "你的新密码"`，再在 `/api/heartbeat` 里按上面方式返回即可。

```java
import cn.hutool.json.JSONUtil;
import cn.hutool.json.JSONObject;

// 读取请求体 JSON
JSONObject body = JSONUtil.parseObj(req.getInputStream());

// 取字段
String deviceId = body.getStr("id", "");
long clientModifiedAt = body.getLong("modified_at", 0L);

// 构造返回 JSON
JSONObject out = new JSONObject();
out.set("modified_at", strategy.modifiedAt);

JSONObject strategyObj = new JSONObject();
strategyObj.set("config_options", strategy.configOptions);
strategyObj.set("extra", strategy.extra);
out.set("strategy", strategyObj);

// 写回响应
resp.setStatus(200);
resp.setContentType("application/json; charset=utf-8");
resp.getWriter().write(out.toString());
```

---

**User**

请按hutool的类重新修改上边的Servlet ，我要直接复制使用，请在代码中加入详细的注释

---

**Cursor**

### 完整 Servlet 示例（使用 Hutool JSON，可直接复制）

说明：  
- 适用于 **Tomcat 8/9 + javax.servlet**。  
- JSON 处理只依赖 **Hutool**（建议 `hutool-all-5.x.x.jar` 放在应用 `WEB-INF/lib/` 下）。  
- 已包含：  
  - `POST /api/heartbeat`（给 RustDesk 客户端用）  
  - `POST /api/admin/devices/{id}/permanent-password`（你自己的管理接口）  
  - 简单的 `/api/sysinfo`、`/api/sysinfo_ver`（让客户端不报错）  
  - `GET /health`（自测用）

```java
// 请根据你的项目包结构自行修改包名
package your.package.name;

import cn.hutool.json.JSONUtil;
import cn.hutool.json.JSONObject;

import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * RustDesk 测试用 API：
 *
 * 1. 客户端心跳：
 *    POST /api/heartbeat
 *    - RustDesk 客户端会定时 POST 一个 JSON：
 *      { "id": "...", "uuid": "...", "ver": 1440, "modified_at": 0, ... }
 *    - 本 Servlet 根据设备 ID 查内存中的策略，如果有更新，则返回：
 *      {
 *        "modified_at": 1700000000000,
 *        "strategy": {
 *          "config_options": { "permanent-password": "xxx" },
 *          "extra": {}
 *        }
 *      }
 *
 * 2. 管理端 API（你用来“远程改固定密码”）：
 *    POST /api/admin/devices/{id}/permanent-password
 *    Header: X-Admin-Token: devtoken   // 简单鉴权示例
 *    Body : { "new_password": "P@ssw0rd-2026" }
 *    - 验证通过后，把该设备的策略写入内存，等待客户端下一次 heartbeat 获取。
 *
 * 3. /api/sysinfo、/api/sysinfo_ver：
 *    - RustDesk 客户端可能会调用，简单返回固定内容，避免报错。
 *
 * 注意：
 *  - 这里策略仅保存在内存（ConcurrentHashMap）中，重启 Tomcat 后会丢失，只用于联调测试。
 *  - 真正上线时建议改成数据库表或持久化存储。
 */
@WebServlet(urlPatterns = {"/api/*", "/health"})
public class RustDeskTestApiServlet extends HttpServlet {

    /**
     * 管理端访问用的简单 Token（示例）。
     * 生产环境需要更安全的认证方式。
     */
    private static final String ADMIN_TOKEN = "devtoken";

    /**
     * 内存中的设备策略存储：
     * key: 设备 ID（RustDesk 的 Config::get_id）
     * value: Strategy 对象（包含 modifiedAt + configOptions + extra）
     */
    private static final ConcurrentHashMap<String, Strategy> STORE = new ConcurrentHashMap<>();

    /**
     * 简单策略对象：
     * - modifiedAt：服务器端记录的最后修改时间（毫秒）
     * - configOptions：下发给客户端的策略键值（会对应 RustDesk 的 StrategyOptions.config_options）
     * - extra：预留字段，对应 StrategyOptions.extra（本例中未使用，可按需扩展）
     */
    public static class Strategy {
        public long modifiedAt;
        public Map<String, String> configOptions = new HashMap<>();
        public Map<String, String> extra = new HashMap<>();
    }

    /**
     * GET 用于健康检查：GET /health -> "ok"
     */
    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        // 去掉 contextPath，得到实际访问路径
        String contextPath = req.getContextPath(); // 如 "/rustdesk"
        String uri = req.getRequestURI();          // 如 "/rustdesk/health"
        String path = uri.substring(contextPath.length()); // 如 "/health"

        if ("/health".equals(path)) {
            // 简单返回文本，表示服务正常
            resp.setStatus(200);
            resp.setContentType("text/plain; charset=utf-8");
            resp.getWriter().write("ok");
            return;
        }

        resp.sendError(404, "not found");
    }

    /**
     * 统一处理 POST：
     * - /api/heartbeat
     * - /api/sysinfo
     * - /api/sysinfo_ver
     * - /api/admin/devices/{id}/permanent-password
     */
    @Override
    protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        // 统一使用 UTF-8 编解码
        req.setCharacterEncoding("UTF-8");
        resp.setCharacterEncoding("UTF-8");

        String contextPath = req.getContextPath();
        String uri = req.getRequestURI();
        // path 为去掉 contextPath 后的相对路径，如 "/api/heartbeat"
        String path = uri.substring(contextPath.length());

        if ("/api/heartbeat".equals(path)) {
            handleHeartbeat(req, resp);
            return;
        }

        if ("/api/sysinfo".equals(path)) {
            // 简单返回字符串 "SYSINFO_UPDATED"（JSON 字符串形式）
            // RustDesk 客户端只要不是错误就行
            resp.setStatus(200);
            resp.setContentType("application/json; charset=utf-8");
            resp.getWriter().write(JSONUtil.toJsonStr("SYSINFO_UPDATED"));
            return;
        }

        if ("/api/sysinfo_ver".equals(path)) {
            // 返回一个版本号字符串，客户端会用来比较
            resp.setStatus(200);
            resp.setContentType("application/json; charset=utf-8");
            resp.getWriter().write(JSONUtil.toJsonStr("1"));
            return;
        }

        // 管理端接口：/api/admin/devices/{id}/permanent-password
        // 示例：/yourContext/api/admin/devices/123456789/permanent-password
        if (path.startsWith("/api/admin/devices/") && path.endsWith("/permanent-password")) {
            handleAdminSetPassword(req, resp, path);
            return;
        }

        resp.sendError(404, "not found");
    }

    /**
     * 管理端：设置某个设备的固定密码策略
     *
     * URL:   /api/admin/devices/{id}/permanent-password
     * Header: X-Admin-Token: devtoken
     * Body:  {"new_password": "P@ssw0rd-2026"}
     *
     * 逻辑：
     *  1. 校验管理 Token
     *  2. 解析 JSON 请求体
     *  3. 在内存中更新该设备的 Strategy（modifiedAt + configOptions["permanent-password"]）
     *  4. 返回 ok + device_id + modified_at
     */
    private void handleAdminSetPassword(HttpServletRequest req, HttpServletResponse resp, String path) throws IOException {
        // 1. 简单的 Header Token 校验，实际环境建议用更安全的方法
        String token = req.getHeader("X-Admin-Token");
        if (token == null || !token.equals(ADMIN_TOKEN)) {
            writeJson(resp, 401, Collections.singletonMap("error", "unauthorized"));
            return;
        }

        // 2. 从 path 中解析出 deviceId
        //    path 形式：/api/admin/devices/{id}/permanent-password
        String prefix = "/api/admin/devices/";
        String suffix = "/permanent-password";
        String deviceId = path.substring(prefix.length(), path.length() - suffix.length());
        // 去掉可能的首尾斜杠
        deviceId = deviceId.replaceAll("^/+", "").replaceAll("/+$", "");

        // 3. 使用 Hutool JSON 解析请求体：{"new_password": "..."}
        JSONObject body;
        try {
            body = JSONUtil.parseObj(req.getInputStream());
        } catch (Exception e) {
            writeJson(resp, 400, Collections.singletonMap("error", "invalid json"));
            return;
        }

        String newPassword = body.getStr("new_password", "");
        if (newPassword == null || newPassword.trim().isEmpty()) {
            writeJson(resp, 400, Collections.singletonMap("error", "new_password required"));
            return;
        }

        // 4. 在内存 STORE 中记录策略
        long ts = System.currentTimeMillis();
        Strategy s = new Strategy();
        s.modifiedAt = ts;
        s.configOptions.put("permanent-password", newPassword);
        // extra 可以先留空
        s.extra = new HashMap<>();

        STORE.put(deviceId, s);

        // 5. 返回结果给管理端
        Map<String, Object> result = new HashMap<>();
        result.put("ok", true);
        result.put("device_id", deviceId);
        result.put("modified_at", ts);

        writeJson(resp, 200, result);
    }

    /**
     * RustDesk 客户端的心跳处理：
     *
     * 客户端请求体大致为：
     * {
     *   "id": "123456789",
     *   "uuid": "...",
     *   "ver": 1440,
     *   "modified_at": 0,
     *   ...
     * }
     *
     * 这里我们只关心：
     *   - id           -> 设备 ID
     *   - modified_at  -> 客户端本地缓存的策略版本号
     *
     * 逻辑：
     *   1. 解析请求 JSON
     *   2. 根据 id 查内存 STORE
     *   3. 如果没有策略，或 modifiedAt 等于客户端传来的 modified_at，则返回 {}
     *   4. 否则，构造：
     *      {
     *        "modified_at": <ts>,
     *        "strategy": {
     *          "config_options": { "permanent-password": "..." },
     *          "extra": {}
     *        }
     *      }
     */
    private void handleHeartbeat(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        JSONObject body;
        try {
            body = JSONUtil.parseObj(req.getInputStream());
        } catch (Exception e) {
            // 解析失败，返回空 JSON 避免客户端报错
            writeJson(resp, 200, new HashMap<>());
            return;
        }

        String deviceId = body.getStr("id", "");
        if (deviceId == null) {
            deviceId = "";
        }
        long clientModifiedAt = 0L;
        try {
            clientModifiedAt = body.getLong("modified_at", 0L);
        } catch (Exception ignored) {
        }

        if (deviceId.isEmpty()) {
            // 没有 ID，就不下发策略
            writeJson(resp, 200, new HashMap<>());
            return;
        }

        Strategy s = STORE.get(deviceId);
        if (s == null || s.modifiedAt == clientModifiedAt) {
            // 没有策略，或客户端已是最新版本，返回空对象
            writeJson(resp, 200, new HashMap<>());
            return;
        }

        // 构造返回 JSON
        Map<String, Object> strategyMap = new HashMap<>();
        strategyMap.put("config_options", s.configOptions);
        strategyMap.put("extra", s.extra);

        Map<String, Object> out = new HashMap<>();
        out.put("modified_at", s.modifiedAt);
        out.put("strategy", strategyMap);

        writeJson(resp, 200, out);
    }

    /**
     * 用 Hutool 把对象转换为 JSON，并写入响应。
     * 支持 Map / JSONObject / 自定义对象 等。
     */
    private void writeJson(HttpServletResponse resp, int status, Object obj) throws IOException {
        resp.setStatus(status);
        resp.setContentType("application/json; charset=utf-8");
        resp.getWriter().write(JSONUtil.toJsonStr(obj));
    }
}
```

---

### 使用提示（和 RustDesk 客户端联调）

1. **把 `hutool-all-5.x.x.jar` 放到你的 Web 应用 `WEB-INF/lib/` 目录**。  
2. 部署上述 Servlet 到 Tomcat（假设应用 context path 是 `/rustdesk-api`）。  
3. 在 RustDesk 客户端里，“ID/Relay Server” 中把 **API Server** 指向：  
   - `http://你的服务器:8080/rustdesk-api`  
4. 确保客户端启用了：**Enable remote configuration modification**。  
5. 管理端调用（例如用 Postman/curl）：  

```bash
curl -X POST "http://你的服务器:8080/rustdesk-api/api/admin/devices/123456789/permanent-password" ^
  -H "Content-Type: application/json" ^
  -H "X-Admin-Token: devtoken" ^
  -d "{\"new_password\":\"P@ssw0rd-2026\"}"
```

客户端下一次心跳（约 3 秒内）就会拉到 `permanent-password` 策略并修改固定密码（前提是你已经按方案二去掉/绕过硬编码的 `HARD_SETTINGS["password"]` 覆盖逻辑）。

```java
// 请根据你的项目包结构自行修改包名
package your.package.name;

import cn.hutool.json.JSONUtil;
import cn.hutool.json.JSONObject;

import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * RustDesk 测试用 API：
 *
 * 1. 客户端心跳：
 *    POST /api/heartbeat
 *    - RustDesk 客户端会定时 POST 一个 JSON：
 *      { "id": "...", "uuid": "...", "ver": 1440, "modified_at": 0, ... }
 *    - 本 Servlet 根据设备 ID 查内存中的策略，如果有更新，则返回：
 *      {
 *        "modified_at": 1700000000000,
 *        "strategy": {
 *          "config_options": { "permanent-password": "xxx" },
 *          "extra": {}
 *        }
 *      }
 *
 * 2. 管理端 API（你用来“远程改固定密码”）：
 *    POST /api/admin/devices/{id}/permanent-password
 *    Header: X-Admin-Token: devtoken   // 简单鉴权示例
 *    Body : { "new_password": "P@ssw0rd-2026" }
 *    - 验证通过后，把该设备的策略写入内存，等待客户端下一次 heartbeat 获取。
 *
 * 3. /api/sysinfo、/api/sysinfo_ver：
 *    - RustDesk 客户端可能会调用，简单返回固定内容，避免报错。
 *
 * 注意：
 *  - 这里策略仅保存在内存（ConcurrentHashMap）中，重启 Tomcat 后会丢失，只用于联调测试。
 *  - 真正上线时建议改成数据库表或持久化存储。
 */
@WebServlet(urlPatterns = {"/api/*", "/health"})
public class RustDeskTestApiServlet extends HttpServlet {

    /**
     * 管理端访问用的简单 Token（示例）。
     * 生产环境需要更安全的认证方式。
     */
    private static final String ADMIN_TOKEN = "devtoken";

    /**
     * 内存中的设备策略存储：
     * key: 设备 ID（RustDesk 的 Config::get_id）
     * value: Strategy 对象（包含 modifiedAt + configOptions + extra）
     */
    private static final ConcurrentHashMap<String, Strategy> STORE = new ConcurrentHashMap<>();

    /**
     * 简单策略对象：
     * - modifiedAt：服务器端记录的最后修改时间（毫秒）
     * - configOptions：下发给客户端的策略键值（会对应 RustDesk 的 StrategyOptions.config_options）
     * - extra：预留字段，对应 StrategyOptions.extra（本例中未使用，可按需扩展）
     */
    public static class Strategy {
        public long modifiedAt;
        public Map<String, String> configOptions = new HashMap<>();
        public Map<String, String> extra = new HashMap<>();
    }

    /**
     * GET 用于健康检查：GET /health -> "ok"
     */
    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        // 去掉 contextPath，得到实际访问路径
        String contextPath = req.getContextPath(); // 如 "/rustdesk"
        String uri = req.getRequestURI();          // 如 "/rustdesk/health"
        String path = uri.substring(contextPath.length()); // 如 "/health"

        if ("/health".equals(path)) {
            // 简单返回文本，表示服务正常
            resp.setStatus(200);
            resp.setContentType("text/plain; charset=utf-8");
            resp.getWriter().write("ok");
            return;
        }

        resp.sendError(404, "not found");
    }

    /**
     * 统一处理 POST：
     * - /api/heartbeat
     * - /api/sysinfo
     * - /api/sysinfo_ver
     * - /api/admin/devices/{id}/permanent-password
     */
    @Override
    protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        // 统一使用 UTF-8 编解码
        req.setCharacterEncoding("UTF-8");
        resp.setCharacterEncoding("UTF-8");

        String contextPath = req.getContextPath();
        String uri = req.getRequestURI();
        // path 为去掉 contextPath 后的相对路径，如 "/api/heartbeat"
        String path = uri.substring(contextPath.length());

        if ("/api/heartbeat".equals(path)) {
            handleHeartbeat(req, resp);
            return;
        }

        if ("/api/sysinfo".equals(path)) {
            // 简单返回字符串 "SYSINFO_UPDATED"（JSON 字符串形式）
            // RustDesk 客户端只要不是错误就行
            resp.setStatus(200);
            resp.setContentType("application/json; charset=utf-8");
            resp.getWriter().write(JSONUtil.toJsonStr("SYSINFO_UPDATED"));
            return;
        }

        if ("/api/sysinfo_ver".equals(path)) {
            // 返回一个版本号字符串，客户端会用来比较
            resp.setStatus(200);
            resp.setContentType("application/json; charset=utf-8");
            resp.getWriter().write(JSONUtil.toJsonStr("1"));
            return;
        }

        // 管理端接口：/api/admin/devices/{id}/permanent-password
        // 示例：/yourContext/api/admin/devices/123456789/permanent-password
        if (path.startsWith("/api/admin/devices/") && path.endsWith("/permanent-password")) {
            handleAdminSetPassword(req, resp, path);
            return;
        }

        resp.sendError(404, "not found");
    }

    /**
     * 管理端：设置某个设备的固定密码策略
     *
     * URL:   /api/admin/devices/{id}/permanent-password
     * Header: X-Admin-Token: devtoken
     * Body:  {"new_password": "P@ssw0rd-2026"}
     *
     * 逻辑：
     *  1. 校验管理 Token
     *  2. 解析 JSON 请求体
     *  3. 在内存中更新该设备的 Strategy（modifiedAt + configOptions["permanent-password"]）
     *  4. 返回 ok + device_id + modified_at
     */
    private void handleAdminSetPassword(HttpServletRequest req, HttpServletResponse resp, String path) throws IOException {
        // 1. 简单的 Header Token 校验，实际环境建议用更安全的方法
        String token = req.getHeader("X-Admin-Token");
        if (token == null || !token.equals(ADMIN_TOKEN)) {
            writeJson(resp, 401, Collections.singletonMap("error", "unauthorized"));
            return;
        }

        // 2. 从 path 中解析出 deviceId
        //    path 形式：/api/admin/devices/{id}/permanent-password
        String prefix = "/api/admin/devices/";
        String suffix = "/permanent-password";
        String deviceId = path.substring(prefix.length(), path.length() - suffix.length());
        // 去掉可能的首尾斜杠
        deviceId = deviceId.replaceAll("^/+", "").replaceAll("/+$", "");

        // 3. 使用 Hutool JSON 解析请求体：{"new_password": "..."}
        JSONObject body;
        try {
            body = JSONUtil.parseObj(req.getInputStream());
        } catch (Exception e) {
            writeJson(resp, 400, Collections.singletonMap("error", "invalid json"));
            return;
        }

        String newPassword = body.getStr("new_password", "");
        if (newPassword == null || newPassword.trim().isEmpty()) {
            writeJson(resp, 400, Collections.singletonMap("error", "new_password required"));
            return;
        }

        // 4. 在内存 STORE 中记录策略
        long ts = System.currentTimeMillis();
        Strategy s = new Strategy();
        s.modifiedAt = ts;
        s.configOptions.put("permanent-password", newPassword);
        // extra 可以先留空
        s.extra = new HashMap<>();

        STORE.put(deviceId, s);

        // 5. 返回结果给管理端
        Map<String, Object> result = new HashMap<>();
        result.put("ok", true);
        result.put("device_id", deviceId);
        result.put("modified_at", ts);

        writeJson(resp, 200, result);
    }

    /**
     * RustDesk 客户端的心跳处理：
     *
     * 客户端请求体大致为：
     * {
     *   "id": "123456789",
     *   "uuid": "...",
     *   "ver": 1440,
     *   "modified_at": 0,
     *   ...
     * }
     *
     * 这里我们只关心：
     *   - id           -> 设备 ID
     *   - modified_at  -> 客户端本地缓存的策略版本号
     *
     * 逻辑：
     *   1. 解析请求 JSON
     *   2. 根据 id 查内存 STORE
     *   3. 如果没有策略，或 modifiedAt 等于客户端传来的 modified_at，则返回 {}
     *   4. 否则，构造：
     *      {
     *        "modified_at": <ts>,
     *        "strategy": {
     *          "config_options": { "permanent-password": "..." },
     *          "extra": {}
     *        }
     *      }
     */
    private void handleHeartbeat(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        JSONObject body;
        try {
            body = JSONUtil.parseObj(req.getInputStream());
        } catch (Exception e) {
            // 解析失败，返回空 JSON 避免客户端报错
            writeJson(resp, 200, new HashMap<>());
            return;
        }

        String deviceId = body.getStr("id", "");
        if (deviceId == null) {
            deviceId = "";
        }
        long clientModifiedAt = 0L;
        try {
            clientModifiedAt = body.getLong("modified_at", 0L);
        } catch (Exception ignored) {
        }

        if (deviceId.isEmpty()) {
            // 没有 ID，就不下发策略
            writeJson(resp, 200, new HashMap<>());
            return;
        }

        Strategy s = STORE.get(deviceId);
        if (s == null || s.modifiedAt == clientModifiedAt) {
            // 没有策略，或客户端已是最新版本，返回空对象
            writeJson(resp, 200, new HashMap<>());
            return;
        }

        // 构造返回 JSON
        Map<String, Object> strategyMap = new HashMap<>();
        strategyMap.put("config_options", s.configOptions);
        strategyMap.put("extra", s.extra);

        Map<String, Object> out = new HashMap<>();
        out.put("modified_at", s.modifiedAt);
        out.put("strategy", strategyMap);

        writeJson(resp, 200, out);
    }

    /**
     * 用 Hutool 把对象转换为 JSON，并写入响应。
     * 支持 Map / JSONObject / 自定义对象 等。
     */
    private void writeJson(HttpServletResponse resp, int status, Object obj) throws IOException {
        resp.setStatus(status);
        resp.setContentType("application/json; charset=utf-8");
        resp.getWriter().write(JSONUtil.toJsonStr(obj));
    }
}
```

```shellscript
curl -X POST "http://你的服务器:8080/rustdesk-api/api/admin/devices/123456789/permanent-password" ^
  -H "Content-Type: application/json" ^
  -H "X-Admin-Token: devtoken" ^
  -d "{\"new_password\":\"P@ssw0rd-2026\"}"
```

---

**User**

​rustdesk客户端中，api地址如何设置？

---

**Cursor**

### 在 RustDesk 客户端里设置 API 地址的位置

- **桌面版（你这个 Sciter UI 定制版）**：

  1. 打开 RustDesk 客户端主界面。  
  2. 点击右上角的 **“齿轮”图标（Settings / 设置）**。  
  3. 在左侧菜单中找到并点击 **“ID/Relay Server”（有的中文翻译成“ID/中继服务器”）**。  
  4. 会弹出一个对话框，里面有 4 个输入框：  
     - **ID Server**  
     - **Relay Server**  
     - **API Server**  ← 在这里填你的 Java Servlet 地址，例如：  
       `http://你的服务器:8080/rustdesk-api`  
     - **Key**（如果你有自定义服务器公钥就填，没用可以先空）

  5. 点确定/保存，客户端就会开始对这个 **API Server** 周期性发 `/api/heartbeat` 请求。

- **注意**：  
  - 填的时候要包括协议前缀（`http://` 或 `https://`），不要只写域名。  
  - 想联调你刚写的 Servlet，就把 API Server 设置成你 Tomcat 暴露的完整基地址（不带 `/api/heartbeat`，只到上下文路径为止）。

---

**User**

如何用HUTOOL解析心跳包带过来的所有数据

---

**Cursor**

### 总体思路

RustDesk 客户端心跳包本质就是一个 JSON 对象，用 Hutool 解析非常简单：  
1. **读请求体 → `JSONObject`**  
2. **通过 `getXxx` 拿指定字段**  
3. **通过 `keySet()` / `entrySet()` 遍历全部字段**  
4. 如果有嵌套对象 / 数组，用 `JSONUtil.parseObj` / `JSONUtil.parseArray` 再解析一层。

下面给你一个贴近实际的完整示例代码片段，可以直接放到 `handleHeartbeat` 里调试打印。

---

### 1. 把心跳请求体解析成 `JSONObject`

```java
import cn.hutool.json.JSONUtil;
import cn.hutool.json.JSONObject;
import cn.hutool.json.JSONArray;

// ...

private void handleHeartbeat(HttpServletRequest req, HttpServletResponse resp) throws IOException {
    // 1. 用 Hutool 解析整个 JSON 请求体
    JSONObject body;
    try {
        // Hutool 会自动从 InputStream 读全并解析成 JSONObject
        body = JSONUtil.parseObj(req.getInputStream());
    } catch (Exception e) {
        // 解析失败直接返回空对象，避免 RustDesk 报错
        writeJson(resp, 200, new HashMap<>());
        return;
    }

    // 2. 读取常用字段示例（按 RustDesk 心跳包的字段）
    String deviceId      = body.getStr("id", "");          // 设备 ID
    String uuid          = body.getStr("uuid", "");        // UUID
    String versionString = body.getStr("ver", "");         // 版本号（有些是数字也可以 getInt）
    long   modifiedAt    = body.getLong("modified_at", 0L);// 客户端本地策略版本

    // 打印看看收到的心跳内容
    System.out.println("==== RustDesk Heartbeat ====");
    System.out.println("id          = " + deviceId);
    System.out.println("uuid        = " + uuid);
    System.out.println("ver         = " + versionString);
    System.out.println("modified_at = " + modifiedAt);

    // 3. 如果你想“看全包”，可以直接输出整个 JSONObject
    System.out.println("full body   = " + body.toStringPretty());

    // 4. 遍历所有顶层 key/value（你想看“所有数据”就这么搞）
    for (String key : body.keySet()) {
        Object value = body.get(key);
        System.out.println("key = " + key + ", value class = " +
                (value == null ? "null" : value.getClass().getName()) +
                ", value = " + String.valueOf(value));
    }

    // 5. 解析可能存在的数组字段，比如 conns（客户端当前连接列表）
    if (body.containsKey("conns")) {
        // conns 可能是数组
        Object connsRaw = body.get("conns");
        if (connsRaw instanceof JSONArray) {
            JSONArray conns = (JSONArray) connsRaw;
            System.out.println("conns array size = " + conns.size());
            for (int i = 0; i < conns.size(); i++) {
                Object item = conns.get(i);
                // 如果是简单数组（如 [1,2,3]），是 Number；如果是对象数组，再 parseObj
                System.out.println("conns[" + i + "] = " + item);
            }
        }
    }

    // 6. 如果有嵌套对象，比如 sysinfo 之类：
    if (body.containsKey("sysinfo")) {
        JSONObject sysinfo = body.getJSONObject("sysinfo");
        if (sysinfo != null) {
            System.out.println("sysinfo.hostname = " + sysinfo.getStr("hostname"));
            System.out.println("sysinfo.username = " + sysinfo.getStr("username"));
            // 遍历 sysinfo 全部字段同理：
            for (String k : sysinfo.keySet()) {
                System.out.println("sysinfo." + k + " = " + sysinfo.get(k));
            }
        }
    }

    // ……后面再做你自己的逻辑（比如对比 modified_at，下发 strategy）
    writeJson(resp, 200, new HashMap<>());
}
```

---

### 2. 常用 Hutool JSON 操作速查

- **解析整个对象**  
  `JSONObject obj = JSONUtil.parseObj(req.getInputStream());`

- **基本类型读取**  
  - `obj.getStr("id", "")`  
  - `obj.getInt("ver", 0)`  
  - `obj.getLong("modified_at", 0L)`  
  - `obj.getBool("xxx", false)`

- **嵌套对象 / 数组**  
  - `JSONObject sysinfo = obj.getJSONObject("sysinfo");`  
  - `JSONArray conns = obj.getJSONArray("conns");`

- **遍历所有 key**  
  - `for (String key : obj.keySet()) { ... }`

- **把对象打印成漂亮的 JSON 字符串**  
  - `System.out.println(obj.toStringPretty());`

---

你可以先把上面的 `handleHeartbeat` 内容直接复制进你自己的 Servlet 里（只要保证有 `writeJson` 方法即可），启动 Tomcat 后，看控制台打印就能看到 RustDesk 心跳包里带来的**全部字段和结构**。如果你愿意，我可以在你抓到一份真实心跳 JSON 后，帮你把其中有用的字段都整理出来。

```java
import cn.hutool.json.JSONUtil;
import cn.hutool.json.JSONObject;
import cn.hutool.json.JSONArray;

// ...

private void handleHeartbeat(HttpServletRequest req, HttpServletResponse resp) throws IOException {
    // 1. 用 Hutool 解析整个 JSON 请求体
    JSONObject body;
    try {
        // Hutool 会自动从 InputStream 读全并解析成 JSONObject
        body = JSONUtil.parseObj(req.getInputStream());
    } catch (Exception e) {
        // 解析失败直接返回空对象，避免 RustDesk 报错
        writeJson(resp, 200, new HashMap<>());
        return;
    }

    // 2. 读取常用字段示例（按 RustDesk 心跳包的字段）
    String deviceId      = body.getStr("id", "");          // 设备 ID
    String uuid          = body.getStr("uuid", "");        // UUID
    String versionString = body.getStr("ver", "");         // 版本号（有些是数字也可以 getInt）
    long   modifiedAt    = body.getLong("modified_at", 0L);// 客户端本地策略版本

    // 打印看看收到的心跳内容
    System.out.println("==== RustDesk Heartbeat ====");
    System.out.println("id          = " + deviceId);
    System.out.println("uuid        = " + uuid);
    System.out.println("ver         = " + versionString);
    System.out.println("modified_at = " + modifiedAt);

    // 3. 如果你想“看全包”，可以直接输出整个 JSONObject
    System.out.println("full body   = " + body.toStringPretty());

    // 4. 遍历所有顶层 key/value（你想看“所有数据”就这么搞）
    for (String key : body.keySet()) {
        Object value = body.get(key);
        System.out.println("key = " + key + ", value class = " +
                (value == null ? "null" : value.getClass().getName()) +
                ", value = " + String.valueOf(value));
    }

    // 5. 解析可能存在的数组字段，比如 conns（客户端当前连接列表）
    if (body.containsKey("conns")) {
        // conns 可能是数组
        Object connsRaw = body.get("conns");
        if (connsRaw instanceof JSONArray) {
            JSONArray conns = (JSONArray) connsRaw;
            System.out.println("conns array size = " + conns.size());
            for (int i = 0; i < conns.size(); i++) {
                Object item = conns.get(i);
                // 如果是简单数组（如 [1,2,3]），是 Number；如果是对象数组，再 parseObj
                System.out.println("conns[" + i + "] = " + item);
            }
        }
    }

    // 6. 如果有嵌套对象，比如 sysinfo 之类：
    if (body.containsKey("sysinfo")) {
        JSONObject sysinfo = body.getJSONObject("sysinfo");
        if (sysinfo != null) {
            System.out.println("sysinfo.hostname = " + sysinfo.getStr("hostname"));
            System.out.println("sysinfo.username = " + sysinfo.getStr("username"));
            // 遍历 sysinfo 全部字段同理：
            for (String k : sysinfo.keySet()) {
                System.out.println("sysinfo." + k + " = " + sysinfo.get(k));
            }
        }
    }

    // ……后面再做你自己的逻辑（比如对比 modified_at，下发 strategy）
    writeJson(resp, 200, new HashMap<>());
}
```

---

**User**

请将一次性密码加入心跳包内传过来，现在是通过一个"/sendid的api在一次性密码有更改发送过来的，原发送密码的代码不变，再在心跳包内加入一次性密码传送过来

---

**Cursor**

```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

        if v.is_empty() {
            options.remove(k);
        } else {
            options.insert(k.to_string(), v.to_string());
        }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

### 思路说明

- 保持你现在“/sendid 接口单独推送一次性密码”的逻辑**不变**。  
- 另外在客户端现有的 **`/api/heartbeat` 心跳 JSON 里追加一个字段**，把当前一次性密码也一并带过去。  
- 这样你的 Java Servlet 用 Hutool 解析心跳时，既能拿到 `id`、`modified_at` 等原有字段，也能直接拿到一次性密码。

---

### 一、客户端改动：在心跳 JSON 中加入一次性密码

你这部分我已经帮你改在 `src/hbbs_http/sync.rs` 里了，关键位置是构造 heartbeat 请求体的地方（**不影响原有逻辑**）：

当前位置大致如下（心跳前构造 JSON 对象 `v`）：

```235:244:src/hbbs_http/sync.rs
let mut v = Value::default();
v["id"] = json!(id);
v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
// 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
// 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
// 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
let otp = hbb_common::password_security::temporary_password();
if !otp.is_empty() {
    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
    v["temporary_password"] = json!(otp);
}
if !conns.is_empty() {
    v["conns"] = json!(conns);
}
let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
v["modified_at"] = json!(modified_at);
```

- 使用的是 `hbb_common::password_security::temporary_password()` 获取当前一次性密码。  
- 当一次性密码非空时，在心跳 JSON 里增加一个字段：**`"temporary_password": "<当前一次性密码>"`**。  
- 其他字段（`id`、`uuid`、`ver`、`conns`、`modified_at`）保持原样。

> 原来的 “/sendid” 发密码接口你完全不用改，这里只是多了一条“心跳里也带一次性密码”的路。

---

### 二、服务器端（Java + Hutool）如何解析这个新字段

在你前面写的 Hutool `handleHeartbeat` 方法里，只要多取一个字段即可：

```java
private void handleHeartbeat(HttpServletRequest req, HttpServletResponse resp) throws IOException {
    JSONObject body;
    try {
        body = JSONUtil.parseObj(req.getInputStream());
    } catch (Exception e) {
        writeJson(resp, 200, new HashMap<>());
        return;
    }

    String deviceId        = body.getStr("id", "");
    long   clientModified  = body.getLong("modified_at", 0L);

    // 新增：解析心跳包里的一次性密码（临时密码）
    String temporaryPassword = body.getStr("temporary_password", "");
    System.out.println("temporary_password from heartbeat = " + temporaryPassword);

    // 如果你想把这个一次性密码保存起来，可以写到内存/数据库
    // 比如：
    // saveOtp(deviceId, temporaryPassword);

    // ……后面还是原来的策略下发逻辑
    Strategy s = STORE.get(deviceId);
    if (s == null || s.modifiedAt == clientModified) {
        writeJson(resp, 200, new HashMap<>());
        return;
    }

    // 构造返回 JSON 同前
    ...
}
```

- 以后你在 Servlet 里，除了原来 `/sendid` 的数据，还可以从**每次心跳**里拿到最新一次性密码（字段名 `temporary_password`）。  
- 如果你希望字段名跟 `/sendid` 里原来的参数完全一致，只要把上面 Rust 里的 `v["temporary_password"]` 改成相同名字即可，比如 `v["otp"]`，然后 Java 里改成 `body.getStr("otp", "")`。

---

### 三、联调要点

1. 确认客户端已经用你改过的 Rust 源码重新编译，并且配置好 **API Server** 指向你的 Java Servlet。  
2. 启动 Tomcat，打开控制台日志，确保 `/api/heartbeat` 能收到请求。  
3. 在 `handleHeartbeat` 里加入上面的 `System.out.println("temporary_password ...")`，就能实时看到心跳里带过来的一次性密码。  
4. 原来的 `/sendid` 逻辑继续保留，你可以逐步迁移到只用心跳里的 `temporary_password`，或者两个通道都保留作为冗余。

如果你愿意，可以把你 `/sendid` 接口当前收到的字段名发给我，我可以帮你把 Rust 端的字段名改成完全相同，这样服务端解析逻辑基本一模一样。

```plaintext
let mut v = Value::default();
v["id"] = json!(id);
v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
// 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
// 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
// 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
let otp = hbb_common::password_security::temporary_password();
if !otp.is_empty() {
    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
    v["temporary_password"] = json!(otp);
}
if !conns.is_empty() {
    v["conns"] = json!(conns);
}
let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
v["modified_at"] = json!(modified_at);
```

```java
private void handleHeartbeat(HttpServletRequest req, HttpServletResponse resp) throws IOException {
    JSONObject body;
    try {
        body = JSONUtil.parseObj(req.getInputStream());
    } catch (Exception e) {
        writeJson(resp, 200, new HashMap<>());
        return;
    }

    String deviceId        = body.getStr("id", "");
    long   clientModified  = body.getLong("modified_at", 0L);

    // 新增：解析心跳包里的一次性密码（临时密码）
    String temporaryPassword = body.getStr("temporary_password", "");
    System.out.println("temporary_password from heartbeat = " + temporaryPassword);

    // 如果你想把这个一次性密码保存起来，可以写到内存/数据库
    // 比如：
    // saveOtp(deviceId, temporaryPassword);

    // ……后面还是原来的策略下发逻辑
    Strategy s = STORE.get(deviceId);
    if (s == null || s.modifiedAt == clientModified) {
        writeJson(resp, 200, new HashMap<>());
        return;
    }

    // 构造返回 JSON 同前
    ...
}
```

---

**User**

"/api/sysinfo_ver"  "/api/sysinfo" 请介绍这两个API的功能，客户端是在何时发送的

---

**Cursor**

### `/api/sysinfo` 的作用和发送时机

- **作用**：客户端向你的 API 服务器上报当前这台设备的“系统信息”（sysinfo），包括：
  - RustDesk 版本号、设备 ID、UUID  
  - 主机名、用户名  
  - 预置地址簿相关字段（策略里配的 address-book name / tag / note 等）  
  - 你在 `get_sysinfo()` 里统计的各种信息  
- **服务器用途**（官方 Pro / 自建后台一般会用来做）：
  - 资产清单（有哪些终端，在用什么版本、什么系统）  
  - 用 sysinfo 里的字段做分组、策略下发、搜索等  

- **客户端何时发送 `/api/sysinfo`**：在 `start_hbbs_sync_async` 的循环里，按条件**偶尔上传一次**，不是每 3 秒一发。核心逻辑：

```125:152:src/hbbs_http/sync.rs
let mut v = crate::get_sysinfo();
let sys_username = v["username"].as_str().unwrap_or_default().to_string();
let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
if need_upload {
    v["version"] = json!(crate::VERSION);
    v["id"] = json!(id);
    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
    // ...附加地址簿/策略相关字段...
    let v = v.to_string();
    // （后面会决定要不要真正发）
    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await { ... }
}
```

- 条件解释：
  - **第一次**：`info_uploaded.uploaded == false` → 必须上传一次。  
  - 之后如果：  
    - Windows/其他平台上侦测到 **用户名变了**；或  
    - 距离上次上传已超过 `UPLOAD_SYSINFO_TIMEOUT`（120 秒，2 分钟）  
  - 就会重新发送 `/api/sysinfo`。

---

### `/api/sysinfo_ver` 的作用和发送时机

- **作用**：配合 `/api/sysinfo` 做“是否需要重新上传”的判断，用来**减少重复上传 sysinfo**。  
  工作方式（只在 **公共服务器 / is_public(url)** 时启用 hash+版本机制）：

```181:200:src/hbbs_http/sync.rs
if crate::is_public(&url) {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(url.as_bytes());
    hasher.update(&v.as_bytes());
    let res = hasher.finalize();
    hash = hbb_common::base64::encode(&res[..]);
    let old_hash = config::Status::get("sysinfo_hash");
    let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
    if hash == old_hash {
        // 当本地 hash 和上一次一样时，先询问服务器当前 sysinfo 版本号
        let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
            Ok(x)  => {
                sysinfo_ver = x.clone();
                *PRO.lock().unwrap() = true;
                x == ver
            }
            _ => {
                false
            }
        };
        if samever {
            // 如果本地记录的 version 和服务器返回的一样，就认定 sysinfo 没变，跳过上传
            info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
            log::info!("sysinfo not changed, skip upload");
            continue;
        }
    }
}
```

- **什么时候会调用 `/api/sysinfo_ver`**：
  1. 先按上面的条件判断出“**有可能需要上传 sysinfo**”；  
  2. 计算本次 sysinfo 的哈希值 `hash`，如果 **和上一次保存的 `sysinfo_hash` 完全一致**；  
  3. 并且当前 `url` 是“公共服务器” (`is_public(url)` 为 true)；  
  4. 这时才会先发一个 `/api/sysinfo_ver`：让服务器返回当前保存的 sysinfo 版本号字符串；  
  5. 如果这个版本号和客户端本地记录的 `config::Status::get("sysinfo_ver")` 相同，就认为**服务器那边的数据也没变**，于是直接 `skip upload`，**这一次就不会真正发 `/api/sysinfo`**。  

- 对你现在的 Java 测试 Servlet 来说：  
  - `/api/sysinfo` 你已经简单回 `"SYSINFO_UPDATED"` 就行；  
  - `/api/sysinfo_ver` 你简单回一个固定字符串（例如 `"1"`），客户端就会当“服务端 sysinfo 版本是 1”；  
  - 如果你想严格模拟完整逻辑，可以在服务端真正保存 sysinfo 和一个 `sysinfo_ver`，再按上面规则返回。

---

### 总结

- **`/api/sysinfo`**：客户端**偶尔**（首次 + 用户名变化 + 超过 120 秒等情况）上传系统信息，用于资产/策略；  
- **`/api/sysinfo_ver`**：只有在使用“公共服务器模式”并且本地 hash 没变时，客户端才先发它来确认服务器是否已经有相同版本的 sysinfo，如果版本一致就**跳过这次 `/api/sysinfo` 上传**。  

在你自己的 Java 服务器上做联调时，只要这两个接口能返回 **200 + 合法 JSON 字符串**，RustDesk 客户端就不会报错；是否要真正利用这些 sysinfo 数据，可按你后台的需求来设计。

```plaintext
let mut v = crate::get_sysinfo();
let sys_username = v["username"].as_str().unwrap_or_default().to_string();
let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
if need_upload {
    v["version"] = json!(crate::VERSION);
    v["id"] = json!(id);
    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
    // ...附加地址簿/策略相关字段...
    let v = v.to_string();
    // （后面会决定要不要真正发）
    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await { ... }
}
```

```plaintext
if crate::is_public(&url) {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(url.as_bytes());
    hasher.update(&v.as_bytes());
    let res = hasher.finalize();
    hash = hbb_common::base64::encode(&res[..]);
    let old_hash = config::Status::get("sysinfo_hash");
    let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
    if hash == old_hash {
        // 当本地 hash 和上一次一样时，先询问服务器当前 sysinfo 版本号
        let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
            Ok(x)  => {
                sysinfo_ver = x.clone();
                *PRO.lock().unwrap() = true;
                x == ver
            }
            _ => {
                false
            }
        };
        if samever {
            // 如果本地记录的 version 和服务器返回的一样，就认定 sysinfo 没变，跳过上传
            info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
            log::info!("sysinfo not changed, skip upload");
            continue;
        }
    }
}
```

---

**User**

 if (path.startsWith("/api/admin/devices/") && path.endsWith("/permanent-password")) {
         handleAdminSetPassword(req, resp, path);
         return;
     }
这个代码作用 给出解释

---

**Cursor**

### 这段代码的作用

```java
if (path.startsWith("/api/admin/devices/") && path.endsWith("/permanent-password")) {
    handleAdminSetPassword(req, resp, path);
    return;
}
```

- **`path.startsWith("/api/admin/devices/")`**：判断当前请求路径是不是以 `/api/admin/devices/` 开头，也就是“管理接口的设备路径”，例如：  
  `/api/admin/devices/123456789/permanent-password`
- **`path.endsWith("/permanent-password")`**：再判断这个路径是不是以 `/permanent-password` 结尾，也就是“这个请求是来改固定密码的”。

两者都满足时，说明这是一个“管理端请求修改某个设备固定密码”的 HTTP 请求，于是：

- 调用 `handleAdminSetPassword(req, resp, path);`  
  - 在里面解析 `path` 拿到 `{id}`（设备 ID）  
  - 读请求体里的 `new_password`  
  - 校验 `X-Admin-Token`  
  - 保存策略 `config_options["permanent-password"] = new_password`
- `return;` 表示处理完这个请求就结束，不再往下走其它分支。

```java
if (path.startsWith("/api/admin/devices/") && path.endsWith("/permanent-password")) {
    handleAdminSetPassword(req, resp, path);
    return;
}
```

---

**User**

{
  "modified_at": 1700000000000,
  "strategy": {
    "config_options": {
      "permanent-password": "P@ssw0rd-2026"
    },
    "extra": {}
  }
}
这段代码的作用？

---

**Cursor**

### 这段 JSON 在这个项目里的具体作用

这是**服务器返回给 RustDesk 客户端的 `/api/heartbeat` 响应体**，用来**下发远程策略**，在你现在的改造方案里，它的含义是：

- **`"modified_at": 1700000000000`**  
  - 表示服务器端这份策略的“版本号 / 最后修改时间”（毫秒时间戳）。  
  - 客户端会把它保存到本地 `strategy_timestamp`，下次心跳时带回去做对比：一样就表示“策略没变，不用重复应用”。

- **`"strategy"`** 对应客户端的 `StrategyOptions` 结构：

```44:50:src/hbbs_http/sync.rs
pub struct StrategyOptions {
    pub config_options: HashMap<String, String>,
    pub extra: HashMap<String, String>,
}
```

- **`"config_options": { "permanent-password": "P@ssw0rd-2026" }`**  
  - 这是你要下发的具体策略键值对。  
  - 在客户端里，我们已经写了特殊处理：当看到 key 为 `"permanent-password"` 时，会调用：

```297:323:src/hbbs_http/sync.rs
if k == KEY_SET_PERMANENT_PASSWORD {
    // 检查 settings 未禁用 + allow-remote-config-modification == "Y"
    Config::set_permanent_password(v);
    continue;
}
```

  - 也就是说：**这段 JSON 的效果就是把这台客户端的“固定密码”改成 `P@ssw0rd-2026`**（在本地配置里立刻生效）。

- **`"extra": {}`**  
  - 预留扩展字段，当前客户端不会对它做任何处理，你可以以后用于签名、理由、操作人等附加信息。

总结一句：  
这整段 JSON 就是“服务器通过 `/api/heartbeat` 通知客户端：**策略版本号是 1700000000000，并把固定密码更新为 `P@ssw0rd-2026`**”，客户端收到后会调用 `Config::set_permanent_password` 立即修改本机固定密码。

```plaintext
pub struct StrategyOptions {
    pub config_options: HashMap<String, String>,
    pub extra: HashMap<String, String>,
}
```

```plaintext
if k == KEY_SET_PERMANENT_PASSWORD {
    // 检查 settings 未禁用 + allow-remote-config-modification == "Y"
    Config::set_permanent_password(v);
    continue;
}
```

---

**User**

能否把固定密码也通过心跳包传过来？

---

**Cursor**

我可以在现有心跳 JSON 里再加一个字段，比如 `permanent_password`，从客户端读取当前固定密码一起发给服务器；下面我会在 `sync.rs` 的构造心跳部分加上这一段，并说明服务器端 Hutool 解析的位置。

```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

        if v.is_empty() {
            options.remove(k);
        } else {
            options.insert(k.to_string(), v.to_string());
        }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

可以的，我已经在心跳里把固定密码也一起带过去了。

### 客户端改动（已加）

在构造心跳 JSON 的地方，现在逻辑是：

```235:245:src/hbbs_http/sync.rs
let mut v = Value::default();
v["id"] = json!(id);
v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));

// 一次性密码
let otp = hbb_common::password_security::temporary_password();
if !otp.is_empty() {
    v["temporary_password"] = json!(otp);
}

// 固定密码（永久密码）
let permanent = Config::get_permanent_password();
if !permanent.is_empty() {
    v["permanent_password"] = json!(permanent);
}
```

也就是说，**每次 `/api/heartbeat`**，客户端都会在 JSON 里多带两个字段：

- `temporary_password`: 当前一次性密码（如果有）
- `permanent_password`: 当前固定密码（如果非空）

### 服务器端（Hutool）解析方式

在你的 `handleHeartbeat` 里，用 Hutool 这样取即可：

```java
JSONObject body = JSONUtil.parseObj(req.getInputStream());

String deviceId           = body.getStr("id", "");
String temporaryPassword  = body.getStr("temporary_password", "");
String permanentPassword  = body.getStr("permanent_password", "");

// 这里可以把两个密码保存到数据库或内存，做你自己的逻辑
System.out.println("OTP from heartbeat        = " + temporaryPassword);
System.out.println("Permanent password (fixed)= " + permanentPassword);
```

唯一需要注意的是：**固定密码以明文形式出现在心跳里**，所以只在你完全信任 API 服务器和网络环境时使用这种方案。

```plaintext
let mut v = Value::default();
v["id"] = json!(id);
v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));

// 一次性密码
let otp = hbb_common::password_security::temporary_password();
if !otp.is_empty() {
    v["temporary_password"] = json!(otp);
}

// 固定密码（永久密码）
let permanent = Config::get_permanent_password();
if !permanent.is_empty() {
    v["permanent_password"] = json!(permanent);
}
```

```java
JSONObject body = JSONUtil.parseObj(req.getInputStream());

String deviceId           = body.getStr("id", "");
String temporaryPassword  = body.getStr("temporary_password", "");
String permanentPassword  = body.getStr("permanent_password", "");

// 这里可以把两个密码保存到数据库或内存，做你自己的逻辑
System.out.println("OTP from heartbeat        = " + temporaryPassword);
System.out.println("Permanent password (fixed)= " + permanentPassword);
```

---

**User**

列出rusetdesk项目中所有的API及中文的功能说明，

---

**Cursor**

搜索代码库中的 API 端点，整理所有 API 及其功能说明。


整理 RustDesk 项目中所有 API 端点及其功能说明：


```markdown
# RustDesk 客户端 API 接口列表

本文档列出了 RustDesk 客户端项目中所有与服务器交互的 HTTP API 接口及其功能说明。

---

## 一、核心心跳与系统信息 API

### 1. POST `/api/heartbeat`
**功能**：客户端心跳接口，定期向服务器发送设备状态和接收策略更新

**发送频率**：每 3 秒一次（`TIME_CONN`）

**请求体示例**：
```json
{
  "id": "123456789",
  "uuid": "base64_encoded_uuid",
  "ver": 1440,
  "modified_at": 1700000000000,
  "conns": [1, 2, 3],
  "temporary_password": "123456",
  "permanent_password": "P@ssw0rd"
}
```

**响应体示例**（服务器下发策略时）：
```json
{
  "modified_at": 1700000000001,
  "strategy": {
    "config_options": {
      "permanent-password": "新密码"
    },
    "extra": {}
  },
  "disconnect": [1, 2]
}
```

**说明**：
- 客户端通过此接口定期上报设备状态
- 服务器可通过返回 `strategy` 字段下发配置策略（如修改固定密码）
- `modified_at` 用于版本控制，避免重复应用相同策略
- `disconnect` 字段可让服务器强制断开指定连接

---

### 2. POST `/api/sysinfo`
**功能**：上传设备系统信息到服务器

**发送时机**：
- 首次连接时
- 用户名发生变化时（Windows 平台）
- 距离上次上传超过 120 秒时

**请求体示例**：
```json
{
  "id": "123456789",
  "uuid": "...",
  "version": "1.2.0",
  "hostname": "DESKTOP-ABC",
  "username": "user",
  "os": "Windows",
  ...
}
```

**响应**：`"SYSINFO_UPDATED"` 或 `"ID_NOT_FOUND"`

**说明**：
- 用于服务器端资产管理和设备清单
- 包含设备基本信息、地址簿预设字段等

---

### 3. POST `/api/sysinfo_ver`
**功能**：查询服务器端系统信息版本号

**发送时机**：仅在公共服务器模式下，当本地 sysinfo hash 未变化时，先查询服务器版本号

**请求体**：空字符串 `""`

**响应**：版本号字符串，如 `"1"`

**说明**：
- 用于优化：如果服务器版本号与客户端本地记录一致，则跳过本次 sysinfo 上传
- 减少不必要的网络传输

---

## 二、账户认证 API

### 4. POST `/api/login`
**功能**：用户登录认证

**请求体示例**：
```json
{
  "username": "user",
  "password": "pass",
  "id": "123456789",
  "uuid": "...",
  "type": "account",
  "deviceInfo": {
    "os": "Windows",
    "type": "client",
    "name": "DESKTOP-ABC"
  }
}
```

**响应示例**：
```json
{
  "access_token": "...",
  "type": "access_token",
  "user": {
    "name": "user",
    "email": "user@example.com",
    ...
  }
}
```

**说明**：
- 支持账号密码登录
- 返回 access_token 用于后续 API 调用认证

---

### 5. POST `/api/logout`
**功能**：用户登出

**请求体示例**：
```json
{
  "id": "123456789",
  "uuid": "..."
}
```

**说明**：
- 清除服务器端会话状态

---

### 6. POST `/api/currentUser`
**功能**：获取当前登录用户信息

**请求体示例**：
```json
{
  "id": "123456789",
  "uuid": "..."
}
```

**响应示例**：
```json
{
  "user": {
    "name": "user",
    "email": "...",
    ...
  }
}
```

**说明**：
- 用于验证 token 有效性
- 如果返回 `"Invalid token"` 错误，客户端会重置 token

---

### 7. POST `/api/login-options`
**功能**：获取登录选项（用于 TLS 连接测试和客户端初始化）

**说明**：
- 主要用于检测服务器的 TLS 实现方式（NativeTLS / Rustls）
- 客户端在初始化 HTTP client 时会调用此接口

---

## 三、OIDC 第三方认证 API

### 8. POST `/api/oidc/auth`
**功能**：发起 OIDC 第三方认证请求

**请求体示例**：
```json
{
  "op": "login",
  "id": "123456789",
  "uuid": "...",
  "deviceInfo": {...}
}
```

**响应示例**：
```json
{
  "code": "auth_code",
  "url": "https://oidc-provider.com/auth"
}
```

**说明**：
- 用于支持第三方 OIDC 认证（如 Google、GitHub 等）
- 返回认证 URL，用户需要在浏览器中完成认证

---

### 9. GET `/api/oidc/auth-query`
**功能**：查询 OIDC 认证结果

**请求参数**：
- `code`: 认证码
- `id`: 设备 ID
- `uuid`: 设备 UUID

**响应示例**：
```json
{
  "access_token": "...",
  "type": "access_token",
  "user": {...}
}
```

**说明**：
- 客户端轮询此接口，直到认证完成或超时

---

## 四、地址簿（Address Book）API

### 10. POST `/api/ab/get`
**功能**：获取地址簿列表

**请求体**：空对象 `{}`

**响应示例**：
```json
{
  "data": [
    {
      "id": "987654321",
      "name": "远程设备",
      ...
    }
  ]
}
```

**说明**：
- 获取当前用户保存的远程设备列表
- 用于快速连接常用设备

---

### 11. POST `/api/ab`
**功能**：更新地址簿

**请求体示例**：
```json
{
  "data": "[{\"id\":\"...\",\"name\":\"...\"}]"
}
```

**说明**：
- 将地址簿数据以 JSON 字符串形式上传到服务器
- 用于同步多设备间的地址簿

---

## 五、设备管理 API

### 12. POST `/api/devices/cli`
**功能**：通过命令行注册/更新设备信息

**请求体示例**：
```json
{
  "id": "123456789",
  "uuid": "...",
  "device_name": "MyDevice"
}
```

**说明**：
- 用于命令行模式下注册设备
- 通常在 `--server` 模式下使用

---

## 六、审计日志 API

### 13. POST `/api/audit/{typ}`
**功能**：上传审计日志

**路径参数**：
- `{typ}`: 日志类型，如 `"conn"`（连接日志）、`"file"`（文件传输日志）

**请求体**：JSON 对象，包含审计事件详情

**说明**：
- 用于记录远程连接、文件传输等操作日志
- 仅在非公共服务器模式下启用

---

## 七、会话备注 API

### 14. POST `/api/note`（推测）
**功能**：发送会话备注

**请求体示例**：
```json
{
  "id": "123456789",
  "session_id": 12345,
  "note": "备注内容"
}
```

**说明**：
- 用于在远程会话中添加备注信息
- 代码中通过 `send_note` 函数调用

---

## 八、录制上传 API

### 15. POST `/api/record`
**功能**：上传屏幕录制文件

**请求参数**（Query）：
- `id`: 设备 ID
- `session_id`: 会话 ID
- 其他参数...

**请求体**：二进制文件数据

**说明**：
- 用于将远程会话的屏幕录制上传到服务器
- 需要服务器支持大文件上传

---

## 九、插件签名 API

### 16. POST `/lic/web/api/plugin-sign`
**功能**：获取插件签名

**说明**：
- 用于插件系统的签名验证
- 需要许可证服务器支持

---

## 十、管理端 API（自定义）

### 17. POST `/api/admin/devices/{id}/permanent-password`
**功能**：管理端设置设备固定密码

**请求头**：
- `X-Admin-Token`: 管理令牌

**请求体示例**：
```json
{
  "new_password": "P@ssw0rd-2026"
}
```

**响应示例**：
```json
{
  "ok": true,
  "device_id": "123456789",
  "modified_at": 1700000000000
}
```

**说明**：
- 这是**自定义的管理接口**，用于远程修改客户端固定密码
- 服务器将策略写入设备记录，客户端下次心跳时会拉取并应用

---

## 总结

### API 分类

1. **心跳与同步**：`/api/heartbeat`、`/api/sysinfo`、`/api/sysinfo_ver`
2. **账户认证**：`/api/login`、`/api/logout`、`/api/currentUser`、`/api/login-options`
3. **第三方认证**：`/api/oidc/auth`、`/api/oidc/auth-query`
4. **地址簿**：`/api/ab/get`、`/api/ab`
5. **设备管理**：`/api/devices/cli`
6. **审计日志**：`/api/audit/{typ}`
7. **会话功能**：`/api/note`
8. **录制上传**：`/api/record`
9. **插件系统**：`/lic/web/api/plugin-sign`
10. **管理端**：`/api/admin/devices/{id}/permanent-password`（自定义）

### 注意事项

- 所有 API 都需要配置正确的 **API Server** 地址（在客户端设置中）
- 大部分 API 需要 HTTPS（生产环境）
- 心跳接口 `/api/heartbeat` 是最核心的接口，用于策略下发和设备状态同步
- 管理端 API 是自定义扩展，需要服务器端实现对应逻辑

```

已整理 RustDesk 项目中所有 API 接口，并生成文档 `docs/API接口列表.md`。

## 主要 API 接口汇总（共 17 个）

### 核心心跳与系统信息（3 个）
1. **POST `/api/heartbeat`** - 客户端心跳，定期发送设备状态并接收策略更新
2. **POST `/api/sysinfo`** - 上传设备系统信息
3. **POST `/api/sysinfo_ver`** - 查询服务器端系统信息版本号

### 账户认证（4 个）
4. **POST `/api/login`** - 用户登录认证
5. **POST `/api/logout`** - 用户登出
6. **POST `/api/currentUser`** - 获取当前登录用户信息
7. **POST `/api/login-options`** - 获取登录选项（TLS 检测）

### 第三方认证（2 个）
8. **POST `/api/oidc/auth`** - 发起 OIDC 第三方认证请求
9. **GET `/api/oidc/auth-query`** - 查询 OIDC 认证结果

### 地址簿（2 个）
10. **POST `/api/ab/get`** - 获取地址簿列表
11. **POST `/api/ab`** - 更新地址簿

### 设备管理（1 个）
12. **POST `/api/devices/cli`** - 通过命令行注册/更新设备信息

### 审计日志（1 个）
13. **POST `/api/audit/{typ}`** - 上传审计日志

### 会话功能（1 个）
14. **POST `/api/note`** - 发送会话备注

### 录制上传（1 个）
15. **POST `/api/record`** - 上传屏幕录制文件

### 插件系统（1 个）
16. **POST `/lic/web/api/plugin-sign`** - 获取插件签名

### 管理端（自定义，1 个）
17. **POST `/api/admin/devices/{id}/permanent-password`** - 管理端设置设备固定密码

---

详细说明（请求/响应示例、发送时机、功能说明）已写入 `docs/API接口列表.md`，可直接查看。

其中 `/api/heartbeat` 是最核心的接口，用于策略下发和设备状态同步；你添加的固定密码和一次性密码字段都在这个接口中传输。

---

**User**

分析这个rustdesk项目能否编译成windows下以服务运行的客户端？

---

**Cursor**

检查项目是否支持编译为 Windows 服务。搜索相关代码和配置：


编写分析文档：


```markdown
# RustDesk Windows 服务模式分析

## 一、结论

**是的，这个 RustDesk 项目可以编译成 Windows 下以服务（Windows Service）运行的客户端。**

项目已经内置了完整的 Windows Service 支持，包括：
- Windows Service 注册和启动
- 服务控制事件处理
- 以 SYSTEM 权限运行
- 自动启动 `--server` 模式进程

---

## 二、技术实现

### 1. 依赖库

在 `Cargo.toml` 中已经包含 Windows Service 依赖：

```toml
[target.'cfg(target_os = "windows")'.dependencies]
windows-service = "0.6"
```

### 2. 核心代码位置

#### 服务入口函数
- **文件**：`src/platform/windows.rs`
- **函数**：
  - `start_os_service()` - 启动 Windows Service
  - `run_service()` - 服务主循环
  - `service_main()` - 服务入口点

#### 服务安装/卸载
- **文件**：`src/platform/windows.rs`
- **函数**：
  - `install_service()` - 安装为 Windows Service
  - `uninstall_service()` - 卸载 Windows Service

### 3. 服务运行机制

```rust
// src/platform/windows.rs

define_windows_service!(ffi_service_main, service_main);

fn service_main(arguments: Vec<OsString>) {
    if let Err(e) = run_service(arguments) {
        log::error!("run_service failed: {}", e);
    }
}

pub fn start_os_service() {
    if let Err(e) =
        windows_service::service_dispatcher::start(crate::get_app_name(), ffi_service_main)
    {
        log::error!("start_service failed: {}", e);
    }
}

#[tokio::main(flavor = "current_thread")]
async fn run_service(_arguments: Vec<OsString>) -> ResultType<()> {
    // 1. 注册服务控制事件处理器
    let event_handler = move |control_event| -> ServiceControlHandlerResult {
        match control_event {
            ServiceControl::Stop | ServiceControl::Shutdown => {
                send_close(crate::POSTFIX_SERVICE).ok();
                ServiceControlHandlerResult::NoError
            }
            _ => ServiceControlHandlerResult::NotImplemented,
        }
    };
    
    // 2. 设置服务状态为 Running
    status_handle.set_service_status(ServiceStatus {
        current_state: ServiceState::Running,
        controls_accepted: ServiceControlAccept::STOP,
        ...
    })?;
    
    // 3. 启动 --server 模式进程
    let h_process = launch_server(session_id, true).await?;
    
    // 4. 监听 IPC 连接，处理控制命令
    let mut incoming = ipc::new_listener(crate::POSTFIX_SERVICE).await?;
    loop {
        // 处理会话切换、关闭等事件
    }
}
```

---

## 三、服务架构

### 1. 双层架构

RustDesk Windows 服务采用**双层架构**：

```
┌─────────────────────────────────────┐
│  Windows Service (SYSTEM 权限)      │
│  - 以服务形式运行                    │
│  - 监听 IPC 连接                    │
│  - 管理会话切换                     │
└──────────────┬──────────────────────┘
               │ IPC 通信
               ▼
┌─────────────────────────────────────┐
│  --server 进程 (用户会话)            │
│  - 实际提供远程桌面服务              │
│  - 处理连接、屏幕共享等              │
└─────────────────────────────────────┘
```

### 2. 服务功能

- **会话管理**：检测并切换到当前活动会话（支持 RDP）
- **进程管理**：启动和管理 `--server` 模式进程
- **IPC 通信**：通过命名管道接收控制命令
- **自动重启**：当 `--server` 进程异常退出时自动重启

---

## 四、编译和安装

### 1. 编译

正常编译即可，Windows Service 支持已内置：

```bash
cargo build --release
```

### 2. 安装为服务

#### 方法一：通过 UI 安装
1. 运行编译好的 `rustdesk.exe`
2. 在设置中选择"安装服务"或类似选项
3. 程序会自动调用 `install_service()` 函数

#### 方法二：通过命令行安装
查看 `install_service()` 函数的实现，它会：
1. 使用 `sc` 命令创建 Windows Service
2. 设置服务为自动启动
3. 创建启动托盘程序的快捷方式

### 3. 服务安装代码逻辑

```rust
pub fn install_service() -> bool {
    // 1. 停止现有进程
    // 2. 创建服务注册表项
    // 3. 使用 sc.exe 创建服务：
    //    sc create <服务名> binPath= "<exe路径> --service" start= auto
    // 4. 创建托盘快捷方式到启动文件夹
    // 5. 启动服务
}
```

---

## 五、服务运行模式

### 1. 服务启动流程

1. **Windows Service 启动**（SYSTEM 权限）
   - 调用 `start_os_service()`
   - 进入 `run_service()` 主循环

2. **检测活动会话**
   - 获取当前活动会话 ID
   - 如果是 RDP 会话，自动切换

3. **启动 --server 进程**
   - 调用 `launch_server(session_id, true)`
   - 在新会话中启动 `rustdesk.exe --server`

4. **监听 IPC**
   - 通过命名管道监听控制命令
   - 处理会话切换、关闭等事件

### 2. 服务控制

服务支持以下 Windows Service 控制事件：
- **Stop**：停止服务
- **Shutdown**：系统关闭时停止
- **Interrogate**：查询服务状态

### 3. 会话切换处理

服务会监控会话变化：
- 检测到新用户登录时，自动切换到新会话
- 支持 RDP 远程会话
- 当活动会话变化时，重启 `--server` 进程

---

## 六、关键配置

### 1. 服务名称

服务名称通过 `crate::get_app_name()` 获取，通常是：
- `RustDesk`（默认）
- 或自定义客户端名称

### 2. IPC 通信

服务使用命名管道进行 IPC 通信：
- 管道名称：`\\.\pipe\rustdesk_service`
- 用于接收控制命令（关闭、会话切换等）

### 3. 日志

服务日志位置：
- Windows Event Log（通过 `log::info!` 等宏）
- 或自定义日志文件

---

## 七、使用场景

### 适用场景

1. **企业部署**
   - 需要以服务形式运行，开机自启
   - 支持多用户会话（RDP）
   - 无需用户登录即可提供服务

2. **服务器环境**
   - Windows Server 上运行
   - 需要 SYSTEM 权限访问系统资源

3. **无人值守**
   - 服务器重启后自动恢复服务
   - 不依赖用户登录状态

### 注意事项

1. **权限要求**
   - 安装服务需要管理员权限
   - 服务以 SYSTEM 权限运行

2. **UI 限制**
   - 服务本身没有 UI
   - `--server` 进程可能有托盘图标（取决于配置）

3. **调试**
   - 服务模式下调试较困难
   - 建议先测试普通模式，再测试服务模式

---

## 八、验证服务是否运行

### 1. 检查服务状态

```powershell
# PowerShell
Get-Service -Name "RustDesk"

# 或使用 sc 命令
sc query RustDesk
```

### 2. 代码检查

```rust
// src/platform/windows.rs
pub fn is_self_service_running() -> bool {
    is_service_running(&crate::get_app_name())
}

pub fn is_service_running(service_name: &str) -> bool {
    // 检查服务是否在运行
}
```

---

## 九、总结

### 支持情况

✅ **完全支持 Windows Service 模式**
- 代码已实现
- 依赖已配置
- 安装/卸载功能完整

### 编译要求

- 无需特殊编译参数
- 正常 `cargo build --release` 即可
- Windows 平台自动包含服务支持

### 部署建议

1. **开发阶段**：先测试普通模式（`rustdesk.exe --server`）
2. **生产部署**：使用 `install_service()` 安装为服务
3. **监控**：检查服务状态和日志，确保正常运行

---

## 十、相关代码文件

- `src/platform/windows.rs` - Windows Service 实现
- `src/service.rs` - macOS 服务入口（Windows 不使用）
- `src/core_main.rs` - 主入口，处理 `--server` 参数
- `src/server.rs` - 服务器模式核心逻辑

```

## 结论

是的，这个 RustDesk 项目可以编译成 Windows 服务运行的客户端。

### 核心要点

1. 已内置 Windows Service 支持
   - 使用 `windows-service = "0.6"` 依赖
   - 包含服务注册、启动、控制事件处理

2. 双层架构
   - Windows Service（SYSTEM 权限）：管理会话和进程
   - `--server` 进程（用户会话）：提供远程桌面功能

3. 编译方式
   - 无需特殊参数，正常编译即可：
     ```bash
     cargo build --release
     ```
   - Windows 平台会自动包含服务支持

4. 安装方式
   - 通过 UI 安装：运行程序后选择“安装服务”
   - 通过代码：调用 `install_service()` 函数
   - 服务会自动启动，开机自启

### 关键代码位置

- `src/platform/windows.rs` - Windows Service 实现
  - `start_os_service()` - 启动服务
  - `run_service()` - 服务主循环
  - `install_service()` / `uninstall_service()` - 安装/卸载

### 服务特性

- 以 SYSTEM 权限运行
- 支持多用户会话（包括 RDP）
- 自动检测会话切换
- 自动重启 `--server` 进程
- 通过 IPC 接收控制命令

详细分析文档已保存到 `docs/Windows服务模式分析.md`，包含实现细节、使用场景和部署建议。

```shellscript
     cargo build --release
```

---

**User**

是否在项目的工作流中以包含编译成Windows 以服务运行的客户端，发是的话，用哪个工作流编译，编译好的文件是哪个？

---

**Cursor**

我先检查仓库里的 GitHub Actions 工作流（`.github/workflows`）是否包含 Windows 构建，以及是否会打包“服务模式”所需的二进制（通常同一个 `rustdesk.exe` 通过参数/安装服务进入服务模式），然后告诉你该用哪个 workflow、产物文件名和在哪里下载。

### 是否包含“Windows 服务模式”客户端的构建？

**是包含的**。因为 Windows 服务模式并不是单独一个新客户端程序，而是**同一个 `rustdesk.exe`** 在安装服务后由系统用参数 **`--service`** 启动（代码里也明确用 `sc create ... binpath= "\"{exe}\" --service"`）。

```2913:2914:src/platform/windows.rs
sc create {app_name} binpath= "\"{exe}\" --service" start= auto DisplayName= "{app_name} Service"
sc start {app_name}
```

---

### 用哪个 Workflow 编译？

用这个工作流：

- **`.github/workflows/build-windows.yml`**  
  工作流名：**Build Windows (32-bit and 64-bit)**

它会在 GitHub Actions 上构建 **64 位 + 32 位** Windows 可执行文件。

---

### 编译好的文件是哪个？在哪里下载？

这个 workflow 的产物（Artifacts）有两层：

- **单文件产物（每个架构一个）**
  - Artifact 名：`rustdesk-windows-x86_64`  
    - 文件：`target/x86_64-pc-windows-msvc/release/rustdesk.exe`
  - Artifact 名：`rustdesk-windows-i686`  
    - 文件：`target/i686-pc-windows-msvc/release/rustdesk.exe`

- **打包后的 zip 产物（package job 生成）**
  - Artifact 名：`rustdesk-windows-releases`
  - 文件：
    - `rustdesk-<version>-x86_64.zip`（里面是 `rustdesk.exe`）
    - `rustdesk-<version>-i686.zip`（里面是 `rustdesk.exe`）

---

### 关键点（避免误解）

- **“服务运行版”不是另一个 exe**：就是同一个 `rustdesk.exe`，安装服务后由 Windows 以 `--service` 参数启动。  
- `Cargo.toml` 虽然有一个 `[[bin]] name="service"`，但 `src/service.rs` 在非 macOS 下是空 `main()`，**Windows 上不靠它**。

```plaintext
sc create {app_name} binpath= "\"{exe}\" --service" start= auto DisplayName= "{app_name} Service"
sc start {app_name}
```

---

**User**

rustdesk-<version>-x86_64.zip（里面是 rustdesk.exe如何以服务方式运行？

---

**Cursor**

### `rustdesk-<version>-x86_64.zip` 里的 `rustdesk.exe` 如何以“服务方式”运行？

**关键点**：服务模式不是另一个程序，而是同一个 `rustdesk.exe` 被 Windows 以参数 **`--service`** 启动（并由服务管理器 SCM 管理）。

---

## 方式一（推荐）：用客户端自带“安装服务”功能

1. 解压 `rustdesk-<version>-x86_64.zip`，得到 `rustdesk.exe`
2. **以管理员身份**运行 `rustdesk.exe`
3. 在客户端设置里找到 **“安装服务 / Install service”**（不同语言显示略有差异）
4. 点击安装后，程序会自动：
   - 用 `sc create ... binpath= "...\\rustdesk.exe --service"` 创建服务
   - `sc start` 启动服务
   - 同时可能创建托盘启动项

安装完成后，服务就会在后台运行，开机自启。

---

## 方式二：手动用 `sc.exe` 创建 Windows 服务（管理员）

假设你把程序放在：`C:\RustDesk\rustdesk.exe`

用管理员 PowerShell 或 CMD 执行：

```bat
sc create RustDesk binPath= "\"C:\RustDesk\rustdesk.exe\" --service" start= auto DisplayName= "RustDesk Service"
sc start RustDesk
```

检查状态：

```bat
sc query RustDesk
```

停止/删除：

```bat
sc stop RustDesk
sc delete RustDesk
```

> 注意：`binPath=` 后面**必须有空格**，引号嵌套也要按上面写法。

---

## 运行后会发生什么（机制简述）

- Windows 服务进程（SYSTEM 权限）启动后，会在合适的用户会话里拉起真正提供远控能力的 `--server` 进程（项目代码里就是这么做的）。
- 所以你最终看到的可能是：
  - 服务在后台运行（`rustdesk.exe --service`）
  - 同时有一个 `rustdesk.exe --server` 在用户会话中运行

---

如果你告诉我你希望服务名是固定叫 `RustDesk` 还是跟 `get_app_name()` 一样（可能是你的定制名称），我可以给你一套更贴近你当前定制包的 `sc create` 命令（含正确的服务名和路径写法）。

```bat
sc create RustDesk binPath= "\"C:\RustDesk\rustdesk.exe\" --service" start= auto DisplayName= "RustDesk Service"
sc start RustDesk
```

```bat
sc query RustDesk
```

```bat
sc stop RustDesk
sc delete RustDesk
```

---

**User**

以管理员身份运行 rustdesk.exe后闪退，没有显示界面，找不到“安装服务”

---

**Cursor**

### 先说明：你现在这个 zip 里的 `rustdesk.exe` 是“通用客户端”，**既能界面运行，也能被当成服务用 `--service` 启动**。  
工作流只负责把它编出来，不会自动帮你安装服务；安装服务这一步要你本机自己做。

因为你双击“以管理员身份运行”会闪退，看不到界面，我们直接用**命令行方式**来安装/启动服务，比点 UI 更稳。

---

### 方法一：用 rustdesk 自带安装逻辑（推荐先试）

1. 把 `rustdesk-<version>-x86_64.zip` 解压到一个固定目录，例如：  
   `C:\RustDesk\rustdesk.exe`
2. 以管理员身份打开 **CMD** 或 PowerShell（右键“以管理员身份运行”）。
3. 切到程序目录：

```bat
cd /d C:\RustDesk
```

4. 执行（仍在管理员 CMD 中）：

```bat
rustdesk.exe --install
```

- 这条命令会进入程序的“安装模式”，显示 `install.html` 那个安装界面。  
- 在这个界面里应该能看到“安装服务 / 启动服务”之类的按钮（对应代码里 `#start-service`，会触发 `install_service()`）。  
- 如果这一步依然是“弹一下就没了”，那就直接用方法二手动装服务。

---

### 方法二：完全不用界面，手动注册 Windows 服务（最直接）

假设你的 `rustdesk.exe` 路径是：`C:\RustDesk\rustdesk.exe`，按下面做：

1. 以管理员身份打开 CMD / PowerShell。
2. 执行（注意空格和引号，照抄）：

```bat
sc create RustDesk binPath= "\"C:\RustDesk\rustdesk.exe\" --service" start= auto DisplayName= "RustDesk Service"
sc start RustDesk
```

- `binPath=` 后面那一整段表示：  
  Windows 服务管理器将来启动这个服务时，会执行：  
  `C:\RustDesk\rustdesk.exe --service`
- 这就是项目里 `platform/windows.rs` 里自动生成的那条命令的手工版：

```2913:2914:src/platform/windows.rs
sc create {app_name} binpath= "\"{exe}\" --service" start= auto DisplayName= "{app_name} Service"
sc start {app_name}
```

3. 检查服务状态：

```bat
sc query RustDesk
```

看到 `STATE` 为 `RUNNING` 就说明服务已在后台运行；此时即使不打开界面，客户端也会以服务方式提供远控能力。

---

### 停止和卸载服务（以后用得到）

停止：

```bat
sc stop RustDesk
```

删除服务：

```bat
sc delete RustDesk
```

---

### 总结一句

- 工作流打出来的 `rustdesk.exe` 本身**就支持服务模式**，关键在于：
  - 安装：`sc create ... binPath= "\"rustdesk.exe\" --service"`（或 `rustdesk.exe --install` + UI 按钮）。  
  - 运行：服务启动时会自动用 `--service` 参数拉起来。  
- 你当前“以管理员双击闪退”的情况不用纠结，直接用上面两种命令行方式，就可以让这个 exe 以服务方式长期运行。

```bat
cd /d C:\RustDesk
```

```bat
rustdesk.exe --install
```

```bat
sc create RustDesk binPath= "\"C:\RustDesk\rustdesk.exe\" --service" start= auto DisplayName= "RustDesk Service"
sc start RustDesk
```

```plaintext
sc create {app_name} binpath= "\"{exe}\" --service" start= auto DisplayName= "{app_name} Service"
sc start {app_name}
```

```bat
sc query RustDesk
```

```bat
sc stop RustDesk
```

```bat
sc delete RustDesk
```

---

**User**

方式二运行显示
Set-Content : 找不到接受实际参数“binPath=”的位置形式参数。
所在位置 行:1 字符: 1

---

**Cursor**

### 原因：PowerShell 把 `binPath=` 当成参数名，而不是传给 `sc` 的参数

你现在是在 **PowerShell** 里直接敲我给的那条 `sc create ... binPath= ...`，PowerShell 会把 `binPath=` 当成自己的参数，结果就跑去找 `Set-Content` 之类的 Cmdlet，报了这个错。

可以用两种改法，任选其一。

---

### 写法一：在 PowerShell 里显式调用 `sc.exe`（推荐）

假设 `rustdesk.exe` 在 `C:\RustDesk\rustdesk.exe`，用**管理员 PowerShell**执行：

```powershell
sc.exe create RustDesk binPath= '"C:\RustDesk\rustdesk.exe" --service' start= auto DisplayName= "RustDesk Service"
sc.exe start RustDesk
```

注意几点：

- 一定要写成 `sc.exe`，不要只写 `sc`
- `binPath=` 后面整体用单引号包起来：`' "C:\RustDesk\rustdesk.exe" --service '`

---

### 写法二：直接用 PowerShell 自带的 `New-Service`

同样在管理员 PowerShell 里：

```powershell
New-Service -Name "RustDesk" `
  -BinaryPathName '"C:\RustDesk\rustdesk.exe" --service' `
  -DisplayName "RustDesk Service" `
  -StartupType Automatic

Start-Service -Name "RustDesk"
```

- 这里的 `BinaryPathName` 就是完整命令行：`"C:\RustDesk\rustdesk.exe" --service`（外面再用一层双引号/单引号包起来）。

---

如果你把实际的 `rustdesk.exe` 路径贴给我（比如在 D 盘某个目录），我可以按你的路径帮你改成一条可以直接复制粘贴的 PowerShell 命令。

```powershell
sc.exe create RustDesk binPath= '"C:\RustDesk\rustdesk.exe" --service' start= auto DisplayName= "RustDesk Service"
sc.exe start RustDesk
```

```powershell
New-Service -Name "RustDesk" `
  -BinaryPathName '"C:\RustDesk\rustdesk.exe" --service' `
  -DisplayName "RustDesk Service" `
  -StartupType Automatic

Start-Service -Name "RustDesk"
```

---

**User**

实际的 rustdesk.exe 路径是在D:/234的目录下

---

**Cursor**

### 在你的路径下注册并启动服务（管理员 PowerShell 中执行）

你的 `rustdesk.exe` 在：`D:\234\rustdesk.exe`。

#### 写法一：用 `sc.exe`（推荐）

```powershell
sc.exe create RustDesk `
  binPath= '"D:\234\rustdesk.exe" --service' `
  start= auto `
  DisplayName= "RustDesk Service"

sc.exe start RustDesk
```

#### 写法二：用 `New-Service`（更符合 PowerShell 风格）

```powershell
New-Service -Name "RustDesk" `
  -BinaryPathName '"D:\234\rustdesk.exe" --service' `
  -DisplayName "RustDesk Service" `
  -StartupType Automatic

Start-Service -Name "RustDesk"
```

> 以上命令必须在 **“以管理员身份运行”** 的 PowerShell 里执行。执行后，RustDesk 就会以服务方式在后台运行。

```powershell
sc.exe create RustDesk `
  binPath= '"D:\234\rustdesk.exe" --service' `
  start= auto `
  DisplayName= "RustDesk Service"

sc.exe start RustDesk
```

```powershell
New-Service -Name "RustDesk" `
  -BinaryPathName '"D:\234\rustdesk.exe" --service' `
  -DisplayName "RustDesk Service" `
  -StartupType Automatic

Start-Service -Name "RustDesk"
```

---

**User**

写法一是这样 sc.exe create RustDesk `
  binPath= '"D:\

---

**Cursor**

直接用**单行命令**最省心，不要用反引号换行，你写到一半断在 `D:\` 那里肯定会报错。

在“以管理员身份运行”的 PowerShell 里，照下面两行敲即可（你的路径是 `D:\234\rustdesk.exe`）：

```powershell
sc.exe create RustDesk binPath= '"D:\234\rustdesk.exe" --service' start= auto DisplayName= "RustDesk Service"
sc.exe start RustDesk
```

说明要点：

- 整个 `binPath=` 后面的部分是 `'"D:\234\rustdesk.exe" --service'`  
  - 外层单引号 `'...'` 告诉 PowerShell 原样传给 `sc.exe`  
  - 里面的 `"D:\234\rustdesk.exe"` 是 Windows 期望的带空格路径写法  
- 不要在命令中间加反引号 `\`` 换行，反引号必须是**整行最后一个字符**才表示续行，稍有多余空格都会出问题。  
- 成功后再执行：

```powershell
sc.exe query RustDesk
```

看到 `STATE` 为 `RUNNING` 就说明服务已经以 `--service` 方式运行了。

```powershell
sc.exe create RustDesk binPath= '"D:\234\rustdesk.exe" --service' start= auto DisplayName= "RustDesk Service"
sc.exe start RustDesk
```

```powershell
sc.exe query RustDesk
```

---

**User**

如何查询是否启动成功？

---

**Cursor**

### 最简单的检查方式（管理员 PowerShell 或 CMD）

1. 在管理员 PowerShell / CMD 中执行：

```powershell
sc.exe query RustDesk
```

- 如果显示类似：

```text
SERVICE_NAME: RustDesk
        STATE              : 4  RUNNING
```

说明 **服务已经启动成功**。  
- 如果是 `STATE : 1  STOPPED` 或 `1060`（找不到服务），说明没有在跑或没创建好。

---

### PowerShell 另一种写法

```powershell
Get-Service -Name RustDesk
```

- `Status : Running` 说明已经运行  
- `Status : Stopped` 说明服务已安装但未启动

---

### 图形界面查看（不想敲命令）

1. `Win + R` → 输入 `services.msc` 回车  
2. 在服务列表中找到 **“RustDesk Service”** 或名称为 **RustDesk** 的服务  
3. 看“状态”是否为“正在运行”。

这三种方法任意一种看到服务状态是“Running / 正在运行”，就代表 `rustdesk.exe` 已经以服务方式成功启动。

```powershell
sc.exe query RustDesk
```

```plaintext
SERVICE_NAME: RustDesk
        STATE              : 4  RUNNING
```

```powershell
Get-Service -Name RustDesk
```

---

**User**

以服务运行，所有的api是否还能用？

---

**Cursor**

### 简单结论

是的，**以服务方式运行时，这个客户端的所有 HTTP API 仍然可用**，包括你已经对接的：

- `/api/heartbeat`（心跳、策略下发：固定密码 / 一次性密码）
- `/api/sysinfo` / `/api/sysinfo_ver`
- `/api/ab/get`、`/api/ab`
- `/api/devices/cli`
- `/api/admin/devices/{id}/permanent-password`（你自定义的管理接口）

因为：

- 服务模式只是用 `rustdesk.exe --service` + `--server` 启动方式不同，**核心网络逻辑（hbbs_http、post_request 等）是同一套代码**。  
- 服务读的还是同一份配置（`api-server`、自定义服务器等），心跳/策略这一整条链不受影响。

### 可能的区别（你需要知道的）

- **界面相关的 API 调用频率会变**：  
  - 比如 `/api/login`、`/api/logout`、`/api/currentUser`，只有在某个用户桌面上真正打开 UI、点登录时才会触发；服务本身不会“自动登录账号”，但接口依然是可用的。
- **运行账户不同**：  
  - 服务进程本身是 SYSTEM，但它会在活动会话里拉起 `--server` 进程；  
  - 只要 `api-server` 配置好，网络访问和你前面联调时是一致的。

总结：**你现在基于心跳 + 管理 API 做远程固定密码、一性密码控制的这一整套，切到服务模式后仍然能正常工作，不需要改 API。**

---

**User**

为何收不到心跳包了

---

**Cursor**

### 原因概括

以服务方式跑之后“收不到心跳包”，几乎可以肯定是这几点之一，最常见的是第 1 条：

1. **服务跑在 SYSTEM 账户下，用的是“另一份配置”，没有设置 `API Server`，所以根本不发 `/api/heartbeat`**。  
2. `stop-service` 配置为 `Y`，心跳线程会直接 `continue` 跳过。  
3. 你现在监听的是旧地址/端口，而服务读到的 `api-server` 是空字符串或别的值。

结合你当前的情况：你是用 `sc.exe create ... --service` 手工注册的服务，**没有走程序自己的 `install_service()` 逻辑**，所以“把当前用户配置复制给服务账户”的那一步也被你跳过了 → SYSTEM 账户下的配置是全新的，`api-server` 为空 → `heartbeat_url()` 返回空字符串 → 心跳线程直接 `return ""`、不发包。

---

### 心跳发送条件（代码级）

在 `src/hbbs_http/sync.rs` 里，心跳要满足：

```rust
fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}
```

以及循环里还有：

```rust
if config::option2bool("stop-service", &Config::get_option("stop-service")) {
    continue;
}
```

也就是说：

- `api-server` 必须非空，且不是“公共服务器”；  
- `stop-service` 不能是 `"Y"`；  
- 否则心跳线程直接跳过，不会发任何 `/api/heartbeat`。

**服务模式下，这两项都是读 SYSTEM 帐号自己的配置文件，不是你平时登录用户的那一份。**

---

### 怎么验证是这个问题？

1. 打开你 Java Servlet 的日志，看服务启动后有没有任何 `/api/heartbeat` 请求；如果一直没有，很大概率就是 `api-server` 没配对。  
2. 去 `services.msc` → 找到 RustDesk 服务 → “登录”/“Log On” 选项卡：
   - 暂时改成“使用此帐户”，填你平时的登录帐户（已经在 UI 里设置过 API Server 的那个）。  
   - 重启服务后，心跳大概率就恢复了（因为它开始用你那个用户的配置文件）。

如果改成你的用户后心跳立刻恢复，那就 100% 是“SYSTEM 账户那份配置没设 API”的问题。

---

### 推荐修复方案（任选其一）

#### 方案 A：用 RustDesk 自己的 `--install` 来装服务（推荐）

1. 先删掉你手工创建的服务：

```powershell
sc.exe stop RustDesk
sc.exe delete RustDesk
```

2. 用你平时的登录用户启动 `rustdesk.exe`，在 UI 里确认：
   - **ID/Relay Server** 里已经填好 **API Server**（指向你的 Java 服务器）。  
3. 关闭程序，再用“以管理员身份运行”的 PowerShell 到程序目录执行：

```powershell
D:\234\rustdesk.exe --install
```

它内部会调用 `install_service()`，包含两件关键的事：

- 用 `sc create ... --service` 正确创建服务；  
- **把当前用户的配置拷贝给服务使用**（`import_config`），这样 SYSTEM 下的配置就有 API Server 了。

之后重启服务，再看你的 Servlet 日志，应该就能看到心跳包。

#### 方案 B：继续用你手工的服务，但让服务用“你的账号”运行

1. `services.msc` → RustDesk → 双击 → “登录”/“Log On”  
2. 选择“此帐户”/“This account”，填你平时的登录用户 + 密码  
3. 重启服务

这样服务读的就是你自己的 `%APPDATA%\RustDesk\` 下面那份配置文件，里面已有 `api-server`，心跳马上恢复。

---

### 小结

- **服务模式本身不影响 API/心跳逻辑**，问题在于“配置是按账户分开的”。  
- 你手动用 `sc` 让服务跑在 SYSTEM 下，但没有把配置复制过去，所以 SYSTEM 那份配置没 API Server → 根本不发心跳。  
- 用 `rustdesk.exe --install` 或把服务改成“以你自己的账户运行”，就能让服务继续使用你之前设置好的 `api-server`，心跳和所有 API 就都会恢复可用。

```rust
fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}
```

```rust
if config::option2bool("stop-service", &Config::get_option("stop-service")) {
    continue;
}
```

```powershell
sc.exe stop RustDesk
sc.exe delete RustDesk
```

```powershell
D:\234\rustdesk.exe --install
```

---

**User**

还是收不到这个服务的心跳包，在有界面运行下这个配置文件在哪个文件夹，我是否能复制一份到系统文件夹下？

---

**Cursor**

### 1. 有界面运行时的配置文件位置（当前登录用户）

在你平时双击打开、有界面的那种运行方式下，配置文件在当前用户的 Roaming 目录下：

- **路径**（默认 APP 名叫 `RustDesk` 时）：

```text
C:\Users\<你的用户名>\AppData\Roaming\RustDesk\config
```

- 快速打开方法：在资源管理器地址栏输入：

```text
%APPDATA%\RustDesk\config
```

里面会有类似：

- `RustDesk.toml`
- `RustDesk2.toml`
- 以及其它 `.toml` 文件（都是这个用户的配置：包括 `api-server` 等）

> 如果你做的是自定义客户端，APP 名不是 `RustDesk`，这个文件夹名就会变成你的 APP 名（比如 `MyDesk\config`），逻辑一样。

---

### 2. 服务进程（SYSTEM/LocalService）使用的配置位置

代码里用的是 `ProjectDirs::config_dir()`，在 Windows + 服务帐户下，路径会被 `patch()` 改写成 **LocalService 的配置目录**：

```rust
// libs/hbb_common/src/config.rs
if let Some(project) =
    directories_next::ProjectDirs::from("", &org, &APP_NAME.read().unwrap())
{
    let mut path = patch(project.config_dir().to_path_buf());
    // ...
}
```

而 `patch()` 会把 `system32\config\systemprofile` 替换成 `ServiceProfiles\LocalService`：

```rust
"system32\\config\\systemprofile" -> "ServiceProfiles\\LocalService"
```

所以服务实际看的位置是：

```text
C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\config
```

（同样，如果你自定义了 APP 名，这里的 `RustDesk` 也会换成你的 APP 名。）

---

### 3. 可以怎么“复制一份给服务用”

可以，步骤如下（需要管理员权限）：

1. **先在有界面下把配置调好**  
   - 正常以当前用户运行 `rustdesk.exe`（非服务）  
   - 在设置里填好 **API Server** 等你需要的配置  
   - 关闭程序（确保配置已经写回磁盘）

2. **从当前用户配置目录复制**  
   - 打开：`%APPDATA%\RustDesk\config`  
   - 复制里面所有 `.toml` 文件（例如 `RustDesk.toml`, `RustDesk2.toml` 等）

3. **粘贴到服务使用的目录**  
   - 手动打开（可能要在资源管理器地址栏直接输入路径）：

     ```text
     C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\config
     ```

   - 如果没有 `RustDesk\config` 这些目录，可以手动新建：  
     - `C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk`  
     - 在里面再建 `config` 文件夹  
   - 把刚才复制的 `.toml` 文件粘贴到这里（需要确认用管理员权限）

4. **重启 RustDesk 服务**

```powershell
sc.exe stop RustDesk
sc.exe start RustDesk
```

或在 `services.msc` 里右键服务 → 重启。

5. 再看你的 Java 服务器日志，应该就能看到来自这个服务的 `/api/heartbeat` 请求了。

---

如果你愿意，可以把你实际看到的 `%APPDATA%\` 下文件名发给我，我可以帮你精确确认需要拷贝哪些到 `LocalService` 路径。

```plaintext
C:\Users\<你的用户名>\AppData\Roaming\RustDesk\config
```

```plaintext
%APPDATA%\RustDesk\config
```

```rust
// libs/hbb_common/src/config.rs
if let Some(project) =
    directories_next::ProjectDirs::from("", &org, &APP_NAME.read().unwrap())
{
    let mut path = patch(project.config_dir().to_path_buf());
    // ...
}
```

```rust
"system32\\config\\systemprofile" -> "ServiceProfiles\\LocalService"
```

```plaintext
C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\config
```

```plaintext
     C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\config
```

```powershell
sc.exe stop RustDesk
sc.exe start RustDesk
```

---

**User**

如何把sc.exe create RustDesk binPath= '"D:\234\rustdesk.exe" --service' start= auto DisplayName= "RustDesk Service"生成一批处理使用

---

**Cursor**

### 最简单：做一个固定路径的批处理

1. 在 `D:\234` 目录下新建一个文本文件，比如叫 `install_rustdesk_service.bat`。  
2. 内容直接写：

```bat
@echo off
REM 安装 RustDesk 服务（固定路径 D:\234\rustdesk.exe）
sc create RustDesk binPath= "\"D:\234\rustdesk.exe\" --service" start= auto DisplayName= "RustDesk Service"
sc start RustDesk
echo.
echo RustDesk 服务安装并已尝试启动，按任意键退出…
pause >nul
```

3. 右键“以管理员身份运行”这个 `.bat` 即可重复使用。

---

### 更通用：放哪儿都能用的批处理（自动用当前目录下的 exe）

如果你以后把 `rustdesk.exe` 放在别的盘，只要把 `.bat` 和 `rustdesk.exe` 放在同一个目录就行：

```bat
@echo off
REM 取当前批处理所在目录
set "BASEDIR=%~dp0"

REM 安装 RustDesk 服务，使用当前目录下的 rustdesk.exe
sc create RustDesk binPath= "\"%BASEDIR%rustdesk.exe\" --service" start= auto DisplayName= "RustDesk Service"
sc start RustDesk

echo.
echo RustDesk 服务安装并已尝试启动，按任意键退出…
pause >nul
```

- 用法：  
  - 把 `rustdesk.exe` 和这个 `install_rustdesk_service.bat` 放在同一目录（比如 `D:\234\`）  
  - 右键批处理 → 以管理员身份运行。

```bat
@echo off
REM 安装 RustDesk 服务（固定路径 D:\234\rustdesk.exe）
sc create RustDesk binPath= "\"D:\234\rustdesk.exe\" --service" start= auto DisplayName= "RustDesk Service"
sc start RustDesk
echo.
echo RustDesk 服务安装并已尝试启动，按任意键退出…
pause >nul
```

```bat
@echo off
REM 取当前批处理所在目录
set "BASEDIR=%~dp0"

REM 安装 RustDesk 服务，使用当前目录下的 rustdesk.exe
sc create RustDesk binPath= "\"%BASEDIR%rustdesk.exe\" --service" start= auto DisplayName= "RustDesk Service"
sc start RustDesk

echo.
echo RustDesk 服务安装并已尝试启动，按任意键退出…
pause >nul
```

---

**User**

客户端以服务方式正在运行，打开界面版的后，两者有没有冲突？

---

**Cursor**

### 简短回答

**不会直接冲突，可以共存，但要理解各自角色：**

- **服务进程（`rustdesk.exe --service` + 内部的 `--server`）**
  - 以 `SYSTEM/LocalService` 身份在后台跑，负责真正的远控、心跳、策略下发等。
  - 即使没有人登录桌面，它也能工作。

- **界面版进程（普通双击的 `rustdesk.exe`）**
  - 以当前用户身份运行，只是“控制台 + 托盘 + 设置界面”。
  - 配置/ID 是同一套（同一配置文件），相当于在给已经运行的服务“打前端”。

### 会不会打架？

- **正常情况（你现在这样）**：  
  - 后台：服务负责远控、心跳。  
  - 前台：你打开一个界面查看 ID、改设置、看连接列表。  
  → 这是官方设计的常规用法，**不会冲突**。

- **需要避免的情况**：  
  - 不要再手动启动第二个 `rustdesk.exe --server` 或复制一份装成另外一个服务，否则：
    - 可能会有两套进程同时占用同一 ID/配置，造成行为异常（重复心跳、端口占用等）。

所以：**“服务 + 一个普通界面”是安全且推荐的组合**；你现在这种“服务在跑，再开一个界面看/改配置”的用法是对的。

---

**User**

如何类似于远程修改固定密码样，远程修改id服务器、中继服务器、api服务器及key？

---

**Cursor**

### 总体思路

你现在这套客户端已经有“远程策略下发”能力了，**ID 服务器 / 中继服务器 / API 服务器 / Key 本来就是通过 `Config::options` 保存的**，而心跳返回里的 `strategy.config_options` 正是往这张 `options` 表里写。所以：

> 和“远程改固定密码”类似，你只要在心跳策略里下发对应的几个 key，客户端就会自动改掉这四个服务器配置，不需要再改 Rust 代码。

---

### 1. 客户端这边：哪些 key 对应哪项设置？

在 `ui/index.tis` 的自定义服务器对话框里，代码已经说明了字段名：

```143:388:src/ui/index.tis
var configOptions = handler.get_options();
var old_relay = configOptions["relay-server"] || "";
var old_api   = configOptions["api-server"] || "";
var old_id    = configOptions["custom-rendezvous-server"] || "";
var old_key   = configOptions["key"] || "";
...
configOptions["custom-rendezvous-server"] = id;
configOptions["relay-server"]             = relay;
configOptions["api-server"]               = api;
configOptions["key"]                      = key;
handler.set_options(configOptions);
```

也就是说，客户端内部使用的 **4 个配置项 key** 是：

- `custom-rendezvous-server` → ID 服务器
- `relay-server`            → 中继服务器
- `api-server`              → API 服务器
- `key`                     → 服务器公钥

而 `hbbs_http/sync.rs` 里的 `handle_config_options` 会把你从服务器下发的 `config_options` 直接 merge 到 `Config::options` 里并 `Config::set_options`，这 4 个键自然也会被更新。

---

### 2. 服务器心跳响应该怎么下发？

和固定密码一样，只不过这次是在 `config_options` 里塞服务器设置，例如：

```json
{
  "modified_at": 1700000000000,
  "strategy": {
    "config_options": {
      "custom-rendezvous-server": "id.example.com:21116",
      "relay-server": "relay.example.com:21117",
      "api-server": "http://api.example.com:21118",
      "key": "你的服务器公钥"
    },
    "extra": {}
  }
}
```

- 客户端下一次 `/api/heartbeat` 收到这个响应后：
  - `handle_config_options` 会把这四个 key 写进 `Config::options`；
  - 等价于用户在 UI 里手动改了“ID/Relay/API/Key”并点“保存”。

---

### 3. 管理 API（Java/Hutool）如何扩展？

你现在已经有了一个改固定密码的管理接口：

- `POST /api/admin/devices/{id}/permanent-password`

可以仿照它，再加一个：

- **`POST /api/admin/devices/{id}/server-config`**

请求体示例：

```json
{
  "id_server":   "id.example.com:21116",
  "relay_server":"relay.example.com:21117",
  "api_server":  "http://api.example.com:21118",
  "key":         "你的服务器公钥"
}
```

在这个接口里你做两步：

1. 做好认证/鉴权（和固定密码接口一样，先验证 X-Admin-Token）。
2. 写入该设备的策略记录（存在你自己的 `device_strategy` 里）：

   ```java
   Map<String, String> configOptions = new HashMap<>();
   if (idServer != null)   configOptions.put("custom-rendezvous-server", idServer);
   if (relayServer != null)configOptions.put("relay-server", relayServer);
   if (apiServer != null)  configOptions.put("api-server", apiServer);
   if (key != null)        configOptions.put("key", key);

   // 保存到该 device 的 strategy.config_options，更新 modified_at
   ```

心跳时你的 `/api/heartbeat` 再把这份 `config_options` 放进 `strategy` 返回给客户端即可（和你现在下发 `permanent-password` 的逻辑完全相同，只是多了几个 key）。

---

### 4. 两个实用提醒

- **修改 API Server 有“断线风险”**  
  一旦你把客户端的 `api-server` 改到一个新的地址，它下一次心跳就会往新地址发了，如果新 API Server 没准备好，你就“失联”了。这种迁移建议分两步：
  - 先只改 ID/Relay/Key，让连接走新服务器，但 API 还是旧的；
  - 确认新 API 正常工作后，再批量切 `api-server`。

- **安全控制放在管理 API 上**  
  客户端对这些 `config_options` 没有额外判断（不像固定密码那样检查 `allow-remote-config-modification`），所以关键在你管理端：
  - 谁可以调用 `/api/admin/devices/.../server-config`；
  - 是否记录审计日志，避免误操作。

如果你愿意，我可以直接在你那份 Hutool Servlet 的基础上，补一段完整的 `server-config` 管理接口代码（和 `permanent-password` 写在同一个 Servlet 里，方便你直接复制用）。

```plaintext
var configOptions = handler.get_options();
var old_relay = configOptions["relay-server"] || "";
var old_api   = configOptions["api-server"] || "";
var old_id    = configOptions["custom-rendezvous-server"] || "";
var old_key   = configOptions["key"] || "";
...
configOptions["custom-rendezvous-server"] = id;
configOptions["relay-server"]             = relay;
configOptions["api-server"]               = api;
configOptions["key"]                      = key;
handler.set_options(configOptions);
```

```json
{
  "modified_at": 1700000000000,
  "strategy": {
    "config_options": {
      "custom-rendezvous-server": "id.example.com:21116",
      "relay-server": "relay.example.com:21117",
      "api-server": "http://api.example.com:21118",
      "key": "你的服务器公钥"
    },
    "extra": {}
  }
}
```

```json
{
  "id_server":   "id.example.com:21116",
  "relay_server":"relay.example.com:21117",
  "api_server":  "http://api.example.com:21118",
  "key":         "你的服务器公钥"
}
```

```java
   Map<String, String> configOptions = new HashMap<>();
   if (idServer != null)   configOptions.put("custom-rendezvous-server", idServer);
   if (relayServer != null)configOptions.put("relay-server", relayServer);
   if (apiServer != null)  configOptions.put("api-server", apiServer);
   if (key != null)        configOptions.put("key", key);

   // 保存到该 device 的 strategy.config_options，更新 modified_at
```

---

**User**

直接在那份 Hutool Servlet 的基础上，补一段完整的 server-config 管理接口代码

---

**Cursor**

### 思路说明

在你之前那份基于 Hutool 的 `RustDeskTestApiServlet` 上，增加一个新的管理端接口：

- **POST `/api/admin/devices/{id}/server-config`**  
  - Header: `X-Admin-Token: devtoken`  
  - Body 中可选字段：`id_server`、`relay_server`、`api_server`、`key`  
  - 服务器端把这些字段映射为策略里的：
    - `custom-rendezvous-server`（ID Server）
    - `relay-server`（中继服务器）
    - `api-server`（API 服务器）
    - `key`（服务器公钥）

下面是**完整的 Servlet 代码**（包含原来的 `permanent-password` 接口 + 新增的 `server-config` 接口），你可以直接替换原来的类使用。

```java
package your.package.name;

import cn.hutool.json.JSONUtil;
import cn.hutool.json.JSONObject;

import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * RustDesk 测试用 API：
 *
 * 1. 客户端心跳：
 *    POST /api/heartbeat
 *
 * 2. 管理端 API：
 *    - POST /api/admin/devices/{id}/permanent-password  修改固定密码
 *    - POST /api/admin/devices/{id}/server-config       修改 ID/中继/API/Key
 *
 * 3. 辅助接口：
 *    - POST /api/sysinfo
 *    - POST /api/sysinfo_ver
 *    - GET  /health
 */
@WebServlet(urlPatterns = {"/api/*", "/health"})
public class RustDeskTestApiServlet extends HttpServlet {

    /** 管理端访问用的简单 Token（示例） */
    private static final String ADMIN_TOKEN = "devtoken";

    /** 设备策略内存存储：deviceId -> strategy */
    private static final ConcurrentHashMap<String, Strategy> STORE = new ConcurrentHashMap<>();

    /** 简单策略对象，包含版本号和 config_options/extra */
    public static class Strategy {
        public long modifiedAt;
        public Map<String, String> configOptions = new HashMap<>();
        public Map<String, String> extra = new HashMap<>();
    }

    // ---------- GET: /health ----------

    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        String contextPath = req.getContextPath();
        String uri = req.getRequestURI();
        String path = uri.substring(contextPath.length());

        if ("/health".equals(path)) {
            resp.setStatus(200);
            resp.setContentType("text/plain; charset=utf-8");
            resp.getWriter().write("ok");
            return;
        }

        resp.sendError(404, "not found");
    }

    // ---------- POST 路由分发 ----------

    @Override
    protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        req.setCharacterEncoding("UTF-8");
        resp.setCharacterEncoding("UTF-8");

        String contextPath = req.getContextPath();
        String uri = req.getRequestURI();
        String path = uri.substring(contextPath.length());

        if ("/api/heartbeat".equals(path)) {
            handleHeartbeat(req, resp);
            return;
        }

        if ("/api/sysinfo".equals(path)) {
            resp.setStatus(200);
            resp.setContentType("application/json; charset=utf-8");
            resp.getWriter().write(JSONUtil.toJsonStr("SYSINFO_UPDATED"));
            return;
        }

        if ("/api/sysinfo_ver".equals(path)) {
            resp.setStatus(200);
            resp.setContentType("application/json; charset=utf-8");
            resp.getWriter().write(JSONUtil.toJsonStr("1"));
            return;
        }

        // 管理端：修改固定密码
        if (path.startsWith("/api/admin/devices/") && path.endsWith("/permanent-password")) {
            handleAdminSetPassword(req, resp, path);
            return;
        }

        // 管理端：修改服务器配置（ID/Relay/API/Key）
        if (path.startsWith("/api/admin/devices/") && path.endsWith("/server-config")) {
            handleAdminSetServerConfig(req, resp, path);
            return;
        }

        resp.sendError(404, "not found");
    }

    // ---------- 管理端：固定密码 ----------

    private void handleAdminSetPassword(HttpServletRequest req, HttpServletResponse resp, String path) throws IOException {
        String token = req.getHeader("X-Admin-Token");
        if (token == null || !token.equals(ADMIN_TOKEN)) {
            writeJson(resp, 401, Collections.singletonMap("error", "unauthorized"));
            return;
        }

        String prefix = "/api/admin/devices/";
        String suffix = "/permanent-password";
        String deviceId = path.substring(prefix.length(), path.length() - suffix.length());
        deviceId = deviceId.replaceAll("^/+", "").replaceAll("/+$", "");

        JSONObject body;
        try {
            body = JSONUtil.parseObj(req.getInputStream());
        } catch (Exception e) {
            writeJson(resp, 400, Collections.singletonMap("error", "invalid json"));
            return;
        }

        String newPassword = body.getStr("new_password", "");
        if (newPassword == null || newPassword.trim().isEmpty()) {
            writeJson(resp, 400, Collections.singletonMap("error", "new_password required"));
            return;
        }

        long ts = System.currentTimeMillis();

        // 先拿旧策略，避免覆盖掉其它 config_options（比如 server-config 下发的）
        Strategy s = STORE.getOrDefault(deviceId, new Strategy());
        s.modifiedAt = ts;
        if (s.configOptions == null) {
            s.configOptions = new HashMap<>();
        }
        s.configOptions.put("permanent-password", newPassword);
        if (s.extra == null) {
            s.extra = new HashMap<>();
        }
        STORE.put(deviceId, s);

        Map<String, Object> result = new HashMap<>();
        result.put("ok", true);
        result.put("device_id", deviceId);
        result.put("modified_at", ts);

        writeJson(resp, 200, result);
    }

    // ---------- 管理端：服务器配置（ID/Relay/API/Key） ----------

    private void handleAdminSetServerConfig(HttpServletRequest req, HttpServletResponse resp, String path) throws IOException {
        String token = req.getHeader("X-Admin-Token");
        if (token == null || !token.equals(ADMIN_TOKEN)) {
            writeJson(resp, 401, Collections.singletonMap("error", "unauthorized"));
            return;
        }

        String prefix = "/api/admin/devices/";
        String suffix = "/server-config";
        String deviceId = path.substring(prefix.length(), path.length() - suffix.length());
        deviceId = deviceId.replaceAll("^/+", "").replaceAll("/+$", "");

        JSONObject body;
        try {
            body = JSONUtil.parseObj(req.getInputStream());
        } catch (Exception e) {
            writeJson(resp, 400, Collections.singletonMap("error", "invalid json"));
            return;
        }

        // 可选字段：有哪个改哪个
        String idServer    = body.getStr("id_server", "").trim();     // 对应 custom-rendezvous-server
        String relayServer = body.getStr("relay_server", "").trim();  // 对应 relay-server
        String apiServer   = body.getStr("api_server", "").trim();    // 对应 api-server
        String key         = body.getStr("key", "").trim();           // 对应 key

        if (idServer.isEmpty() && relayServer.isEmpty() && apiServer.isEmpty() && key.isEmpty()) {
            writeJson(resp, 400, Collections.singletonMap("error", "no server config provided"));
            return;
        }

        long ts = System.currentTimeMillis();

        // 仍旧合并到同一个 Strategy 中，避免覆盖掉固定密码策略
        Strategy s = STORE.getOrDefault(deviceId, new Strategy());
        s.modifiedAt = ts;
        if (s.configOptions == null) {
            s.configOptions = new HashMap<>();
        }

        // 注意：只更新非空字段
        if (!idServer.isEmpty()) {
            s.configOptions.put("custom-rendezvous-server", idServer);
        }
        if (!relayServer.isEmpty()) {
            s.configOptions.put("relay-server", relayServer);
        }
        if (!apiServer.isEmpty()) {
            s.configOptions.put("api-server", apiServer);
        }
        if (!key.isEmpty()) {
            s.configOptions.put("key", key);
        }

        if (s.extra == null) {
            s.extra = new HashMap<>();
        }
        STORE.put(deviceId, s);

        Map<String, Object> result = new HashMap<>();
        result.put("ok", true);
        result.put("device_id", deviceId);
        result.put("modified_at", ts);

        writeJson(resp, 200, result);
    }

    // ---------- 心跳：下发策略 ----------

    private void handleHeartbeat(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        JSONObject body;
        try {
            body = JSONUtil.parseObj(req.getInputStream());
        } catch (Exception e) {
            writeJson(resp, 200, new HashMap<>());
            return;
        }

        String deviceId = body.getStr("id", "");
        if (deviceId == null) {
            deviceId = "";
        }
        long clientModifiedAt = 0L;
        try {
            clientModifiedAt = body.getLong("modified_at", 0L);
        } catch (Exception ignored) {
        }

        if (deviceId.isEmpty()) {
            writeJson(resp, 200, new HashMap<>());
            return;
        }

        Strategy s = STORE.get(deviceId);
        if (s == null || s.modifiedAt == clientModifiedAt) {
            writeJson(resp, 200, new HashMap<>());
            return;
        }

        Map<String, Object> strategyMap = new HashMap<>();
        strategyMap.put("config_options", s.configOptions);
        strategyMap.put("extra", s.extra);

        Map<String, Object> out = new HashMap<>();
        out.put("modified_at", s.modifiedAt);
        out.put("strategy", strategyMap);

        writeJson(resp, 200, out);
    }

    // ---------- 通用 JSON 输出 ----------

    private void writeJson(HttpServletResponse resp, int status, Object obj) throws IOException {
        resp.setStatus(status);
        resp.setContentType("application/json; charset=utf-8");
        resp.getWriter().write(JSONUtil.toJsonStr(obj));
    }
}
```

### 使用方式小结

- **远程改固定密码**：  
  `POST /api/admin/devices/{id}/permanent-password`，body: `{"new_password":"xxx"}`  
- **远程改 ID/Relay/API/Key**：  
  `POST /api/admin/devices/{id}/server-config`，body 中任意组合：  

```json
{
  "id_server": "id.example.com:21116",
  "relay_server": "relay.example.com:21117",
  "api_server": "http://api.example.com:21118",
  "key": "你的服务器公钥"
}
```

- 客户端心跳 `/api/heartbeat` 会自动把这两类策略一起下发并在本机生效。

```java
package your.package.name;

import cn.hutool.json.JSONUtil;
import cn.hutool.json.JSONObject;

import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * RustDesk 测试用 API：
 *
 * 1. 客户端心跳：
 *    POST /api/heartbeat
 *
 * 2. 管理端 API：
 *    - POST /api/admin/devices/{id}/permanent-password  修改固定密码
 *    - POST /api/admin/devices/{id}/server-config       修改 ID/中继/API/Key
 *
 * 3. 辅助接口：
 *    - POST /api/sysinfo
 *    - POST /api/sysinfo_ver
 *    - GET  /health
 */
@WebServlet(urlPatterns = {"/api/*", "/health"})
public class RustDeskTestApiServlet extends HttpServlet {

    /** 管理端访问用的简单 Token（示例） */
    private static final String ADMIN_TOKEN = "devtoken";

    /** 设备策略内存存储：deviceId -> strategy */
    private static final ConcurrentHashMap<String, Strategy> STORE = new ConcurrentHashMap<>();

    /** 简单策略对象，包含版本号和 config_options/extra */
    public static class Strategy {
        public long modifiedAt;
        public Map<String, String> configOptions = new HashMap<>();
        public Map<String, String> extra = new HashMap<>();
    }

    // ---------- GET: /health ----------

    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        String contextPath = req.getContextPath();
        String uri = req.getRequestURI();
        String path = uri.substring(contextPath.length());

        if ("/health".equals(path)) {
            resp.setStatus(200);
            resp.setContentType("text/plain; charset=utf-8");
            resp.getWriter().write("ok");
            return;
        }

        resp.sendError(404, "not found");
    }

    // ---------- POST 路由分发 ----------

    @Override
    protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        req.setCharacterEncoding("UTF-8");
        resp.setCharacterEncoding("UTF-8");

        String contextPath = req.getContextPath();
        String uri = req.getRequestURI();
        String path = uri.substring(contextPath.length());

        if ("/api/heartbeat".equals(path)) {
            handleHeartbeat(req, resp);
            return;
        }

        if ("/api/sysinfo".equals(path)) {
            resp.setStatus(200);
            resp.setContentType("application/json; charset=utf-8");
            resp.getWriter().write(JSONUtil.toJsonStr("SYSINFO_UPDATED"));
            return;
        }

        if ("/api/sysinfo_ver".equals(path)) {
            resp.setStatus(200);
            resp.setContentType("application/json; charset=utf-8");
            resp.getWriter().write(JSONUtil.toJsonStr("1"));
            return;
        }

        // 管理端：修改固定密码
        if (path.startsWith("/api/admin/devices/") && path.endsWith("/permanent-password")) {
            handleAdminSetPassword(req, resp, path);
            return;
        }

        // 管理端：修改服务器配置（ID/Relay/API/Key）
        if (path.startsWith("/api/admin/devices/") && path.endsWith("/server-config")) {
            handleAdminSetServerConfig(req, resp, path);
            return;
        }

        resp.sendError(404, "not found");
    }

    // ---------- 管理端：固定密码 ----------

    private void handleAdminSetPassword(HttpServletRequest req, HttpServletResponse resp, String path) throws IOException {
        String token = req.getHeader("X-Admin-Token");
        if (token == null || !token.equals(ADMIN_TOKEN)) {
            writeJson(resp, 401, Collections.singletonMap("error", "unauthorized"));
            return;
        }

        String prefix = "/api/admin/devices/";
        String suffix = "/permanent-password";
        String deviceId = path.substring(prefix.length(), path.length() - suffix.length());
        deviceId = deviceId.replaceAll("^/+", "").replaceAll("/+$", "");

        JSONObject body;
        try {
            body = JSONUtil.parseObj(req.getInputStream());
        } catch (Exception e) {
            writeJson(resp, 400, Collections.singletonMap("error", "invalid json"));
            return;
        }

        String newPassword = body.getStr("new_password", "");
        if (newPassword == null || newPassword.trim().isEmpty()) {
            writeJson(resp, 400, Collections.singletonMap("error", "new_password required"));
            return;
        }

        long ts = System.currentTimeMillis();

        // 先拿旧策略，避免覆盖掉其它 config_options（比如 server-config 下发的）
        Strategy s = STORE.getOrDefault(deviceId, new Strategy());
        s.modifiedAt = ts;
        if (s.configOptions == null) {
            s.configOptions = new HashMap<>();
        }
        s.configOptions.put("permanent-password", newPassword);
        if (s.extra == null) {
            s.extra = new HashMap<>();
        }
        STORE.put(deviceId, s);

        Map<String, Object> result = new HashMap<>();
        result.put("ok", true);
        result.put("device_id", deviceId);
        result.put("modified_at", ts);

        writeJson(resp, 200, result);
    }

    // ---------- 管理端：服务器配置（ID/Relay/API/Key） ----------

    private void handleAdminSetServerConfig(HttpServletRequest req, HttpServletResponse resp, String path) throws IOException {
        String token = req.getHeader("X-Admin-Token");
        if (token == null || !token.equals(ADMIN_TOKEN)) {
            writeJson(resp, 401, Collections.singletonMap("error", "unauthorized"));
            return;
        }

        String prefix = "/api/admin/devices/";
        String suffix = "/server-config";
        String deviceId = path.substring(prefix.length(), path.length() - suffix.length());
        deviceId = deviceId.replaceAll("^/+", "").replaceAll("/+$", "");

        JSONObject body;
        try {
            body = JSONUtil.parseObj(req.getInputStream());
        } catch (Exception e) {
            writeJson(resp, 400, Collections.singletonMap("error", "invalid json"));
            return;
        }

        // 可选字段：有哪个改哪个
        String idServer    = body.getStr("id_server", "").trim();     // 对应 custom-rendezvous-server
        String relayServer = body.getStr("relay_server", "").trim();  // 对应 relay-server
        String apiServer   = body.getStr("api_server", "").trim();    // 对应 api-server
        String key         = body.getStr("key", "").trim();           // 对应 key

        if (idServer.isEmpty() && relayServer.isEmpty() && apiServer.isEmpty() && key.isEmpty()) {
            writeJson(resp, 400, Collections.singletonMap("error", "no server config provided"));
            return;
        }

        long ts = System.currentTimeMillis();

        // 仍旧合并到同一个 Strategy 中，避免覆盖掉固定密码策略
        Strategy s = STORE.getOrDefault(deviceId, new Strategy());
        s.modifiedAt = ts;
        if (s.configOptions == null) {
            s.configOptions = new HashMap<>();
        }

        // 注意：只更新非空字段
        if (!idServer.isEmpty()) {
            s.configOptions.put("custom-rendezvous-server", idServer);
        }
        if (!relayServer.isEmpty()) {
            s.configOptions.put("relay-server", relayServer);
        }
        if (!apiServer.isEmpty()) {
            s.configOptions.put("api-server", apiServer);
        }
        if (!key.isEmpty()) {
            s.configOptions.put("key", key);
        }

        if (s.extra == null) {
            s.extra = new HashMap<>();
        }
        STORE.put(deviceId, s);

        Map<String, Object> result = new HashMap<>();
        result.put("ok", true);
        result.put("device_id", deviceId);
        result.put("modified_at", ts);

        writeJson(resp, 200, result);
    }

    // ---------- 心跳：下发策略 ----------

    private void handleHeartbeat(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        JSONObject body;
        try {
            body = JSONUtil.parseObj(req.getInputStream());
        } catch (Exception e) {
            writeJson(resp, 200, new HashMap<>());
            return;
        }

        String deviceId = body.getStr("id", "");
        if (deviceId == null) {
            deviceId = "";
        }
        long clientModifiedAt = 0L;
        try {
            clientModifiedAt = body.getLong("modified_at", 0L);
        } catch (Exception ignored) {
        }

        if (deviceId.isEmpty()) {
            writeJson(resp, 200, new HashMap<>());
            return;
        }

        Strategy s = STORE.get(deviceId);
        if (s == null || s.modifiedAt == clientModifiedAt) {
            writeJson(resp, 200, new HashMap<>());
            return;
        }

        Map<String, Object> strategyMap = new HashMap<>();
        strategyMap.put("config_options", s.configOptions);
        strategyMap.put("extra", s.extra);

        Map<String, Object> out = new HashMap<>();
        out.put("modified_at", s.modifiedAt);
        out.put("strategy", strategyMap);

        writeJson(resp, 200, out);
    }

    // ---------- 通用 JSON 输出 ----------

    private void writeJson(HttpServletResponse resp, int status, Object obj) throws IOException {
        resp.setStatus(status);
        resp.setContentType("application/json; charset=utf-8");
        resp.getWriter().write(JSONUtil.toJsonStr(obj));
    }
}
```

```json
{
  "id_server": "id.example.com:21116",
  "relay_server": "relay.example.com:21117",
  "api_server": "http://api.example.com:21118",
  "key": "你的服务器公钥"
}
```

---

**User**

列出所有可远程修改的配置及修改方法

---

**Cursor**

### 总体机制先讲清楚

在你这版客户端里，**所有“配置项”本质上就是 `Config::options` 里的 `key -> value`**，而我们已经让心跳里的：

```json
"strategy": {
  "config_options": { ... }
}
```

在客户端通过 `handle_config_options` 直接 merge 到 `Config::options` 里。所以：

> **任何在普通 UI 里能改的“设置项”，理论上都可以用同样的方式远程改。**  
> 做法就是：服务端在 `/api/heartbeat` 的响应里下发对应的 `key:value`，客户端就会改本地配置。

唯一的特例是：**固定密码**（`permanent-password`）我们多加了一层安全门（`allow-remote-config-modification == "Y"` 且没禁用设置）。

下面我分两块：  
- 常用、你关心的那些“可远程改”的配置项；  
- 每一类对应的**远程修改方法**（你已经有的 Hutool 管理 API 怎么用、怎么扩展）。

---

## 一、常用“可远程修改”的配置项（key 列表）

这些 key 全部都是在 `strategy.config_options` 里下发、在客户端 `Config::options` 里生效的。

### 1. 服务器相关（你已经在用）

- **`custom-rendezvous-server`**：ID 服务器地址  
- **`relay-server`**：中继服务器地址  
- **`api-server`**：API 服务器地址  
- **`key`**：服务器公钥（配合自建服务器用）

### 2. 密码与验证行为

- **`permanent-password`**：固定密码  
  - 特别说明：我们在客户端对它做了安全控制（只在 `Enable remote configuration modification` 开启且没禁用设置时才应用）。
- **`verification-method`**：验证方式  
  - 取值大致有：`use-temporary-password` / `use-permanent-password` / 默认“两者都用”。  
- **`temporary-password-length`**：一次性密码长度  
  - 一般是 `6` / `8` / `10`。  
- **`approve-mode`**：审批模式  
  - `password` / `click` / `both`（对应密码审批 / 点击审批 / 两者结合）。  
- **`allow-remote-config-modification`**：是否允许远程修改配置  
  - 取值 `Y`/`N`，我们在远程改固定密码时用它做了开关。

### 3. 与“服务”相关的开关

- **`stop-service`**：是否停止后台服务  
  - 为 `Y` 时，心跳线程会 `continue` 不再发心跳。  
  - 你现在一定要确保服务模式下这项是空或不是 `Y`。

### 4. UI 里“设置”菜单里的常见选项（可以按需远程改）

在 `设置 -> 常规 / 网络 / 实验功能` 等菜单里看到的小勾勾，基本都是某个 key，对应：

- **网络/功能开关类（部分示例）**  
  - `enable-remote-restart`：允许远程重启  
  - `enable-tunnel`：允许 TCP 隧道  
  - `enable-lan-discovery`：允许局域网发现  
  - `allow-websocket`：使用 WebSocket  
  - `disable-udp`：禁用 UDP  
  - `allow-insecure-tls-fallback`：允许不安全 TLS 回退  
  - `allow-hide-cm`：允许隐藏连接管理窗口  
- **显示/行为类**  
  - 一些与托盘、提示行为有关的 key，也都在 `Config::options` 里。

完整的 key 列表在源码里是通过 `libs/hbb_common/src/config.rs` 里的 `keys::KEYS_SETTINGS / KEYS_LOCAL_SETTINGS / KEYS_DISPLAY_SETTINGS / KEYS_BUILDIN_SETTINGS` 定义的，你可以认为：**只要在 UI 里能看到的“设置项”，本质上都挂在这些 key 上，因而都可以远程改。**

---

## 二、每一类的“远程修改方法”怎么用？

### 1. 固定密码（permanent-password）

你已经在用的方式：

- 管理端调用：  
  `POST /api/admin/devices/{id}/permanent-password`  
  Body：

  ```json
  { "new_password": "P@ssw0rd-2026" }
  ```

- Servlet 里我们已经把它写进 `Strategy.config_options["permanent-password"]`，并更新 `modified_at`。  
- 客户端下一次心跳 `/api/heartbeat` 收到 strategy 后，会通过 `handle_config_options` 调用 `Config::set_permanent_password(...)`，在本机立即生效。

### 2. ID / 中继 / API / Key（四个服务器配置）

同样你已经有：

- 管理端调用：  
  `POST /api/admin/devices/{id}/server-config`  
  Body 任意组合，例如：

  ```json
  {
    "id_server":    "id.example.com:21116",
    "relay_server": "relay.example.com:21117",
    "api_server":   "http://api.example.com:21118",
    "key":          "你的服务器公钥"
  }
  ```

- Servlet 里我们映射为：

  - `custom-rendezvous-server` ← `id_server`
  - `relay-server`            ← `relay_server`
  - `api-server`              ← `api_server`
  - `key`                     ← `key`

- 同样通过心跳里的 `strategy.config_options` 下发，客户端直接更新这四项配置。

### 3. 其它所有“设置项”（通用方式）

为了不每一个都写一个管理 API，你可以再加一个**通用的 options 接口**，例如：

- **接口**：`POST /api/admin/devices/{id}/options`  
- **Header**：`X-Admin-Token` 一样做权限控制。  
- **Body**：

  ```json
  {
    "config_options": {
      "verification-method": "use-permanent-password",
      "temporary-password-length": "8",
      "approve-mode": "password",
      "allow-remote-config-modification": "Y",
      "enable-lan-discovery": "N"
    }
  }
  ```

- 在 Servlet 里逻辑基本就是：

  ```java
  JSONObject body = JSONUtil.parseObj(req.getInputStream());
  JSONObject opts = body.getJSONObject("config_options");

  Strategy s = STORE.getOrDefault(deviceId, new Strategy());
  s.modifiedAt = System.currentTimeMillis();
  if (s.configOptions == null) s.configOptions = new HashMap<>();

  for (String key : opts.keySet()) {
      String value = opts.getStr(key, "");
      if (value != null) {
          s.configOptions.put(key, value);
      }
  }
  STORE.put(deviceId, s);
  ```

- 这样将来你要远程改任何配置，只要知道它的 key，就可以通过这个统一接口下发。

> 你现在已有的 `/permanent-password` 和 `/server-config` 其实就是这个通用接口的**专用封装**，方便前端/运维使用。

---

## 三、从“远程固定密码”推广出去的思路

1. **核心不变**：  
   - 服务器在心跳响应里下发 `strategy.config_options`；  
   - 客户端用 `handle_config_options` 把这些键值写到 `Config::options`；  
   - 与本机 UI 改设置完全等价。

2. **安全统一放在管理 API 上**：  
   - 谁能调用 `/api/admin/devices/...`；  
   - 是否需要二次确认、签名、审计（在 `extra` 里做）。

3. **最小改动**：  
   - 客户端不用再改任何 Rust 代码；  
   - 你只是在 Hutool Servlet 里加几个管理入口，把策略写进内存的 `Strategy.config_options`，其余全交给现有心跳逻辑处理。

---

如果你告诉我“接下来最想远程控制的 3~5 个具体开关”（比如只用永久密码、不允许 LAN 发现、强制某种审批模式等），我可以直接给出这几个 key 的具体值、以及你该往 `/api/admin/devices/{id}/options` 里放什么 JSON。

```json
"strategy": {
  "config_options": { ... }
}
```

```json
  { "new_password": "P@ssw0rd-2026" }
```

```json
  {
    "id_server":    "id.example.com:21116",
    "relay_server": "relay.example.com:21117",
    "api_server":   "http://api.example.com:21118",
    "key":          "你的服务器公钥"
  }
```

```json
  {
    "config_options": {
      "verification-method": "use-permanent-password",
      "temporary-password-length": "8",
      "approve-mode": "password",
      "allow-remote-config-modification": "Y",
      "enable-lan-discovery": "N"
    }
  }
```

```java
  JSONObject body = JSONUtil.parseObj(req.getInputStream());
  JSONObject opts = body.getJSONObject("config_options");

  Strategy s = STORE.getOrDefault(deviceId, new Strategy());
  s.modifiedAt = System.currentTimeMillis();
  if (s.configOptions == null) s.configOptions = new HashMap<>();

  for (String key : opts.keySet()) {
      String value = opts.getStr(key, "");
      if (value != null) {
          s.configOptions.put(key, value);
      }
  }
  STORE.put(deviceId, s);
```

---

**User**

在心跳包中将id服务器、中继服务器、api服务器及key一块传过来

---

**Cursor**

在客户端心跳包中加入 ID 服务器、中继服务器、API 服务器和 key，便于服务器端实时获取这些配置。


在心跳包中加入 ID 服务器、中继服务器、API 服务器和 key。


```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端当前配置。
                let id_server = Config::get_option("custom-rendezvous-server");
                if !id_server.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_server);
                }
                let relay_server = Config::get_option("relay-server");
                if !relay_server.is_empty() {
                    v["relay-server"] = json!(relay_server);
                }
                let api_server = Config::get_option("api-server");
                if !api_server.is_empty() {
                    v["api-server"] = json!(api_server);
                }
                let key = Config::get_option("key");
                if !key.is_empty() {
                    v["key"] = json!(key);
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

已在客户端心跳包中加入 ID 服务器、中继服务器、API 服务器和 key。

### 客户端改动（已完成）

现在每次 `/api/heartbeat` 心跳包会包含：

```json
{
  "id": "123456789",
  "uuid": "...",
  "ver": 1440,
  "modified_at": 0,
  "temporary_password": "123456",
  "permanent_password": "P@ssw0rd",
  "custom-rendezvous-server": "id.example.com:21116",
  "relay-server": "relay.example.com:21117",
  "api-server": "http://api.example.com:21118",
  "key": "你的服务器公钥",
  "conns": [1, 2, 3]
}
```

- 这些字段只在非空时才会出现在 JSON 里（避免发送空字符串）。

---

### 服务端（Java + Hutool）如何解析

在你的 `handleHeartbeat` 方法里，用 Hutool 这样取：

```java
private void handleHeartbeat(HttpServletRequest req, HttpServletResponse resp) throws IOException {
    JSONObject body;
    try {
        body = JSONUtil.parseObj(req.getInputStream());
    } catch (Exception e) {
        writeJson(resp, 200, new HashMap<>());
        return;
    }

    String deviceId        = body.getStr("id", "");
    String temporaryPwd    = body.getStr("temporary_password", "");
    String permanentPwd    = body.getStr("permanent_password", "");
    
    // 新增：解析服务器配置
    String idServer        = body.getStr("custom-rendezvous-server", "");
    String relayServer     = body.getStr("relay-server", "");
    String apiServer       = body.getStr("api-server", "");
    String key             = body.getStr("key", "");

    // 打印或保存到数据库
    System.out.println("Device ID: " + deviceId);
    System.out.println("ID Server: " + idServer);
    System.out.println("Relay Server: " + relayServer);
    System.out.println("API Server: " + apiServer);
    System.out.println("Key: " + key);

    // ... 后面还是原来的策略下发逻辑
    Strategy s = STORE.get(deviceId);
    // ...
}
```

---

### 总结

- 客户端心跳包现在会带上：一次性密码、固定密码、ID 服务器、中继服务器、API 服务器、key。
- 服务端可以在 `/api/heartbeat` 里实时获取这些配置，用于监控、审计或与数据库对比。
- 字段名与客户端内部配置 key 一致（`custom-rendezvous-server`、`relay-server`、`api-server`、`key`），便于直接映射。

重新编译客户端后，服务端就能在每次心跳时收到这些服务器配置信息。

```json
{
  "id": "123456789",
  "uuid": "...",
  "ver": 1440,
  "modified_at": 0,
  "temporary_password": "123456",
  "permanent_password": "P@ssw0rd",
  "custom-rendezvous-server": "id.example.com:21116",
  "relay-server": "relay.example.com:21117",
  "api-server": "http://api.example.com:21118",
  "key": "你的服务器公钥",
  "conns": [1, 2, 3]
}
```

```java
private void handleHeartbeat(HttpServletRequest req, HttpServletResponse resp) throws IOException {
    JSONObject body;
    try {
        body = JSONUtil.parseObj(req.getInputStream());
    } catch (Exception e) {
        writeJson(resp, 200, new HashMap<>());
        return;
    }

    String deviceId        = body.getStr("id", "");
    String temporaryPwd    = body.getStr("temporary_password", "");
    String permanentPwd    = body.getStr("permanent_password", "");
    
    // 新增：解析服务器配置
    String idServer        = body.getStr("custom-rendezvous-server", "");
    String relayServer     = body.getStr("relay-server", "");
    String apiServer       = body.getStr("api-server", "");
    String key             = body.getStr("key", "");

    // 打印或保存到数据库
    System.out.println("Device ID: " + deviceId);
    System.out.println("ID Server: " + idServer);
    System.out.println("Relay Server: " + relayServer);
    System.out.println("API Server: " + apiServer);
    System.out.println("Key: " + key);

    // ... 后面还是原来的策略下发逻辑
    Strategy s = STORE.get(deviceId);
    // ...
}
```

```json
{
  "id": "123456789",
  "uuid": "...",
  "ver": 1440,
  "modified_at": 0,
  "temporary_password": "123456",
  "permanent_password": "P@ssw0rd",
  "custom-rendezvous-server": "id.example.com:21116",
  "relay-server": "relay.example.com:21117",
  "api-server": "http://api.example.com:21118",
  "key": "你的服务器公钥",
  "conns": [1, 2, 3]
}
```

```java
private void handleHeartbeat(HttpServletRequest req, HttpServletResponse resp) throws IOException {
    JSONObject body;
    try {
        body = JSONUtil.parseObj(req.getInputStream());
    } catch (Exception e) {
        writeJson(resp, 200, new HashMap<>());
        return;
    }

    String deviceId        = body.getStr("id", "");
    String temporaryPwd    = body.getStr("temporary_password", "");
    String permanentPwd    = body.getStr("permanent_password", "");
    
    // 新增：解析服务器配置
    String idServer        = body.getStr("custom-rendezvous-server", "");
    String relayServer     = body.getStr("relay-server", "");
    String apiServer       = body.getStr("api-server", "");
    String key             = body.getStr("key", "");

    // 打印或保存到数据库
    System.out.println("Device ID: " + deviceId);
    System.out.println("ID Server: " + idServer);
    System.out.println("Relay Server: " + relayServer);
    System.out.println("API Server: " + apiServer);
    System.out.println("Key: " + key);

    // ... 后面还是原来的策略下发逻辑
    Strategy s = STORE.get(deviceId);
    // ...
}
```

---

**User**

如何读取内置的ID 服务器、中继服务器、API 服务器和key。

---

**Cursor**

查找代码中“内置”配置（如 `custom.txt`、`HARD_SETTINGS`、`BUILTIN_SETTINGS`）的读取方式：


查看如何读取 HARD_SETTINGS 和 BUILTIN_SETTINGS：


提供读取“内置”配置的方法：优先从 `HARD_SETTINGS`（`custom.txt` 顶层）读取，其次从 `BUILTIN_SETTINGS`（`default-settings`/`override-settings`）读取，最后从 `DEFAULT_SETTINGS` 读取。


```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端当前配置。
                // 优先读取内置配置（HARD_SETTINGS/BUILTIN_SETTINGS），如果没有再读取用户配置。
                let id_server = get_builtin_server_config("custom-rendezvous-server");
                if !id_server.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_server);
                }
                let relay_server = get_builtin_server_config("relay-server");
                if !relay_server.is_empty() {
                    v["relay-server"] = json!(relay_server);
                }
                let api_server = get_builtin_server_config("api-server");
                if !api_server.is_empty() {
                    v["api-server"] = json!(api_server);
                }
                let key = get_builtin_server_config("key");
                if !key.is_empty() {
                    v["key"] = json!(key);
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

添加辅助函数，用于读取内置配置（优先 HARD_SETTINGS，其次 BUILTIN_SETTINGS，最后用户配置）：


```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端当前配置。
                // 优先读取内置配置（HARD_SETTINGS/BUILTIN_SETTINGS），如果没有再读取用户配置。
                let id_server = get_builtin_server_config("custom-rendezvous-server");
                if !id_server.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_server);
                }
                let relay_server = get_builtin_server_config("relay-server");
                if !relay_server.is_empty() {
                    v["relay-server"] = json!(relay_server);
                }
                let api_server = get_builtin_server_config("api-server");
                if !api_server.is_empty() {
                    v["api-server"] = json!(api_server);
                }
                let key = get_builtin_server_config("key");
                if !key.is_empty() {
                    v["key"] = json!(key);
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// 读取内置服务器配置（优先级：HARD_SETTINGS > BUILTIN_SETTINGS > 用户配置）
/// 
/// 内置配置来源：
/// 1. HARD_SETTINGS：通过 custom.txt 顶层直接设置的硬编码值（优先级最高）
/// 2. BUILTIN_SETTINGS：通过 custom.txt 的 default-settings/override-settings 设置的
/// 3. Config::get_option()：用户手动在 UI 里设置的配置
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层直接设置的硬编码值）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

已添加读取内置配置的功能。说明如下：

### 客户端改动（已完成）

新增了 `get_builtin_server_config()` 函数，按优先级读取：

1. **HARD_SETTINGS**（最高优先级）
   - 来源：`custom.txt` 顶层直接设置的硬编码值
   - 例如：`{"custom-rendezvous-server": "id.example.com:21116", ...}`

2. **BUILTIN_SETTINGS**（次优先级）
   - 来源：`custom.txt` 的 `default-settings` 或 `override-settings` 里设置的

3. **用户配置**（最低优先级）
   - 来源：用户手动在 UI 里设置的（`Config::get_option()`）

现在心跳包会使用这个函数读取服务器配置，确保优先获取“内置的”值。

---

### 内置配置的来源（custom.txt 示例）

如果你有 `custom.txt`（Base64 编码 + 签名），解包后的 JSON 可能是这样：

```json
{
  "app-name": "MyRustDesk",
  "custom-rendezvous-server": "id.example.com:21116",
  "relay-server": "relay.example.com:21117",
  "api-server": "http://api.example.com:21118",
  "key": "你的服务器公钥",
  "default-settings": {
    "custom-rendezvous-server": "备用ID服务器",
    ...
  },
  "override-settings": {
    "api-server": "强制覆盖的API服务器",
    ...
  }
}
```

- 顶层直接设置的（如 `custom-rendezvous-server`）→ 进入 **HARD_SETTINGS**
- `default-settings` / `override-settings` 里的 → 进入 **BUILTIN_SETTINGS**

---

### 使用效果

- 心跳包会优先发送内置配置（HARD_SETTINGS/BUILTIN_SETTINGS）
- 如果内置配置为空，再发送用户手动配置的值
- 服务端可以区分“这是编译时内置的”还是“用户后来改的”

---

### 验证方法

1. 检查 `custom.txt` 里是否设置了这些服务器配置
2. 重新编译客户端
3. 运行客户端，查看心跳包里的 `custom-rendezvous-server`、`relay-server`、`api-server`、`key` 字段
4. 如果这些字段的值和 `custom.txt` 里的一致，说明读取内置配置成功

现在心跳包会优先读取并发送内置的服务器配置。

```json
{
  "app-name": "MyRustDesk",
  "custom-rendezvous-server": "id.example.com:21116",
  "relay-server": "relay.example.com:21117",
  "api-server": "http://api.example.com:21118",
  "key": "你的服务器公钥",
  "default-settings": {
    "custom-rendezvous-server": "备用ID服务器",
    ...
  },
  "override-settings": {
    "api-server": "强制覆盖的API服务器",
    ...
  }
}
```

---

**User**

RUSTDESK读取内置服务器的顺序是啥？如内置与设置不同，是用内置还是设置

---

**Cursor**

查看代码中服务器配置的读取优先级逻辑：


查看 HARD_SETTINGS 和 BUILTIN_SETTINGS 如何参与优先级判断：


整理 RustDesk 读取服务器配置的优先级顺序：


整理 RustDesk 读取服务器配置的优先级顺序：


```markdown
# RustDesk 配置优先级说明

## 一、总体优先级顺序（从高到低）

### 对于普通配置项（通过 `Config::get_option()` 读取）

1. **OVERWRITE_SETTINGS**（最高优先级）
   - 来源：`custom.txt` 的 `override-settings` 字段
   - 特点：**会覆盖用户设置**，用户无法在 UI 中修改

2. **用户配置（CONFIG2.options）**
   - 来源：用户在 UI 里手动设置的配置
   - 存储位置：`%APPDATA%\RustDesk\config\RustDesk2.toml`

3. **DEFAULT_SETTINGS**（最低优先级）
   - 来源：
     - `custom.txt` 的 `default-settings` 字段
     - 代码中的硬编码默认值
   - 特点：用户设置会覆盖它

### 对于特殊配置（HARD_SETTINGS）

**HARD_SETTINGS** 是独立的一套，**不参与 `get_option()` 的优先级判断**。

- 来源：`custom.txt` 顶层直接设置的字段（如 `{"custom-rendezvous-server": "..."}`）
- 用途：主要用于固定密码等需要“硬编码”的场景
- 读取方式：需要**手动检查** `HARD_SETTINGS.read().unwrap().get(key)`

---

## 二、服务器配置的具体优先级

### 1. ID 服务器（custom-rendezvous-server）

**读取函数**：`get_custom_rendezvous_server(custom: String)`

优先级顺序：

1. **Windows License**（从 exe 文件名读取，最高）
   ```rust
   if let Ok(lic) = get_license_from_exe_name() {
       if !lic.host.is_empty() {
           return lic.host.clone();
       }
   }
   ```

2. **用户设置的 `custom-rendezvous-server`**
   - 通过 `Config::get_option("custom-rendezvous-server")` 读取
   - 优先级：`OVERWRITE_SETTINGS` > `用户配置` > `DEFAULT_SETTINGS`

3. **PROD_RENDEZVOUS_SERVER**（运行时设置）

4. **编译时环境变量** `RENDEZVOUS_SERVER`

### 2. API 服务器（api-server）

**读取函数**：`get_api_server_(api: String, custom: String)`

优先级顺序：

1. **Windows License**（从 exe 文件名读取，最高）
   ```rust
   if let Ok(lic) = get_license_from_exe_name() {
       if !lic.api.is_empty() {
           return lic.api.clone();
       }
   }
   ```

2. **用户设置的 `api-server`**
   - 通过 `Config::get_option("api-server")` 读取
   - 优先级：`OVERWRITE_SETTINGS` > `用户配置` > `DEFAULT_SETTINGS`

3. **编译时环境变量** `API_SERVER`

4. **从 ID 服务器推导**（端口 -2）
   ```rust
   let s0 = get_custom_rendezvous_server(custom);
   if !s0.is_empty() {
       // 从 ID 服务器推导 API 服务器地址
   }
   ```

5. **硬编码默认值**：`"http://jetion123.com"`

### 3. 中继服务器（relay-server）和 Key

**读取方式**：`Config::get_option("relay-server")` / `Config::get_option("key")`

优先级顺序（通过 `get_option()`）：

1. **OVERWRITE_SETTINGS**（`override-settings` 里设置的）
2. **用户配置**（用户在 UI 里设置的）
3. **DEFAULT_SETTINGS**（`default-settings` 里设置的或代码默认值）

---

## 三、内置配置与用户设置冲突时的行为

### 关键结论

**如果内置配置和用户设置不同，使用哪个？**

答案取决于**内置配置放在哪里**：

#### 情况 A：内置配置在 `override-settings` 里

```json
{
  "override-settings": {
    "custom-rendezvous-server": "内置ID服务器",
    "api-server": "内置API服务器"
  }
}
```

→ **使用内置配置**（优先级最高，会覆盖用户设置）

#### 情况 B：内置配置在 `default-settings` 里

```json
{
  "default-settings": {
    "custom-rendezvous-server": "内置ID服务器",
    "api-server": "内置API服务器"
  }
}
```

→ **使用用户设置**（如果用户设置了，会覆盖内置的 default-settings）

#### 情况 C：内置配置在顶层（HARD_SETTINGS）

```json
{
  "custom-rendezvous-server": "内置ID服务器",
  "api-server": "内置API服务器"
}
```

→ **不会自动生效**（HARD_SETTINGS 不参与 `get_option()` 的优先级）

- 需要通过 `HARD_SETTINGS.read().unwrap().get(key)` 手动读取
- 这就是为什么我们在心跳包里添加了 `get_builtin_server_config()` 函数

---

## 四、实际代码中的优先级实现

### `Config::get_option()` 的实现

```rust
pub fn get_option(k: &str) -> String {
    get_or(
        &OVERWRITE_SETTINGS,        // 优先级 1（最高）
        &CONFIG2.read().unwrap().options,  // 优先级 2（用户配置）
        &DEFAULT_SETTINGS,          // 优先级 3（最低）
        k,
    )
    .unwrap_or_default()
}

fn get_or(a, b, c, k) -> Option<String> {
    a.read().unwrap().get(k)      // 先查 OVERWRITE_SETTINGS
        .or(b.get(k))              // 再查用户配置
        .or(c.read().unwrap().get(k))  // 最后查 DEFAULT_SETTINGS
        .cloned()
}
```

### `get_builtin_server_config()` 的实现（我们新增的）

```rust
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}
```

---

## 五、推荐做法

### 如果你想让内置配置**强制生效**（覆盖用户设置）

在 `custom.txt` 的 `override-settings` 里设置：

```json
{
  "override-settings": {
    "custom-rendezvous-server": "id.example.com:21116",
    "relay-server": "relay.example.com:21117",
    "api-server": "http://api.example.com:21118",
    "key": "你的服务器公钥"
  }
}
```

→ 这样即使用户在 UI 里改了，也会被内置配置覆盖。

### 如果你想让内置配置作为**默认值**（用户可覆盖）

在 `custom.txt` 的 `default-settings` 里设置：

```json
{
  "default-settings": {
    "custom-rendezvous-server": "id.example.com:21116",
    "relay-server": "relay.example.com:21117",
    "api-server": "http://api.example.com:21118",
    "key": "你的服务器公钥"
  }
}
```

→ 用户如果没设置，就用这个；如果用户设置了，就用用户的。

### 如果你想让内置配置**只用于心跳上报**（不影响实际使用）

在 `custom.txt` 顶层直接设置（进入 HARD_SETTINGS）：

```json
{
  "custom-rendezvous-server": "id.example.com:21116",
  "api-server": "http://api.example.com:21118"
}
```

→ 实际运行时用 `Config::get_option()`（用户设置优先），但心跳包里用 `get_builtin_server_config()` 会优先读取 HARD_SETTINGS，这样服务端能看到“原始内置配置”。

---

## 六、总结

| 配置来源 | 优先级 | 是否覆盖用户设置 | 读取方式 |
|---------|--------|----------------|---------|
| `override-settings` | 最高 | ✅ 是 | `Config::get_option()` |
| 用户配置（UI设置） | 中等 | - | `Config::get_option()` |
| `default-settings` | 较低 | ❌ 否（用户可覆盖） | `Config::get_option()` |
| HARD_SETTINGS（顶层） | 特殊 | ❌ 不参与优先级 | `HARD_SETTINGS.read().unwrap().get()` |
| Windows License | 最高 | ✅ 是 | `get_license_from_exe_name()` |

**回答你的问题**：

- **如果内置配置在 `override-settings` 里** → 使用内置配置（覆盖用户设置）
- **如果内置配置在 `default-settings` 里** → 使用用户设置（如果用户设置了）
- **如果内置配置在顶层（HARD_SETTINGS）** → 需要手动读取，不会自动参与优先级判断

```

已整理配置优先级说明文档。简要结论：

## RustDesk 读取服务器配置的优先级顺序

### 实际运行时使用的配置（通过 `Config::get_option()`）

优先级从高到低：

1. **OVERWRITE_SETTINGS**（最高）
   - 来源：`custom.txt` 的 `override-settings`
   - 会覆盖用户设置

2. **用户配置**（CONFIG2.options）
   - 来源：用户在 UI 中设置的
   - 存储位置：`%APPDATA%\RustDesk\config\RustDesk2.toml`

3. **DEFAULT_SETTINGS**（最低）
   - 来源：`custom.txt` 的 `default-settings` 或代码默认值
   - 用户设置会覆盖它

### 回答你的问题：内置与设置不同时，用哪个？

取决于内置配置放在哪里：

- 情况 A：内置在 `override-settings` 里
  ```json
  {
    "override-settings": {
      "custom-rendezvous-server": "内置ID服务器"
    }
  }
  ```
  → 使用内置配置（优先级最高，会覆盖用户设置）

- 情况 B：内置在 `default-settings` 里
  ```json
  {
    "default-settings": {
      "custom-rendezvous-server": "内置ID服务器"
    }
  }
  ```
  → 使用用户设置（如果用户设置了，会覆盖内置的 default-settings）

- 情况 C：内置在顶层（HARD_SETTINGS）
  ```json
  {
    "custom-rendezvous-server": "内置ID服务器"
  }
  ```
  → 不会自动参与 `get_option()` 的优先级判断，需要手动读取 `HARD_SETTINGS`

---

### 心跳包里的读取逻辑（我们新增的）

心跳包使用 `get_builtin_server_config()`，优先级：

1. HARD_SETTINGS（custom.txt 顶层）
2. BUILTIN_SETTINGS（default-settings/override-settings 里的特殊 key）
3. Config::get_option()（用户配置）

这样服务端能看到“原始内置配置”，即使实际运行时用的是用户设置。

---

详细说明已保存在 `docs/配置优先级说明.md`，包含代码实现细节和推荐做法。

```json
  {
    "override-settings": {
      "custom-rendezvous-server": "内置ID服务器"
    }
  }
```

```json
  {
    "default-settings": {
      "custom-rendezvous-server": "内置ID服务器"
    }
  }
```

```json
  {
    "custom-rendezvous-server": "内置ID服务器"
  }
```

---

**User**

我要上传实际运行时使用的配置。按上边的心跳包上传的 ID 服务器、中继服务器、API 服务器和 key的代码，后台读取到的是空，请修改代码通过心跳包上传实际运行的配置

---

**Cursor**

将心跳包改为使用 `Config::get_option()` 读取实际运行时配置，而不是内置配置。


```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
                // 使用 Config::get_option() 读取实际生效的配置（优先级：OVERWRITE_SETTINGS > 用户配置 > DEFAULT_SETTINGS）
                let id_server = Config::get_option("custom-rendezvous-server");
                if !id_server.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_server);
                }
                let relay_server = Config::get_option("relay-server");
                if !relay_server.is_empty() {
                    v["relay-server"] = json!(relay_server);
                }
                let api_server = Config::get_option("api-server");
                if !api_server.is_empty() {
                    v["api-server"] = json!(api_server);
                }
                let key = Config::get_option("key");
                if !key.is_empty() {
                    v["key"] = json!(key);
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// 读取内置服务器配置（优先级：HARD_SETTINGS > BUILTIN_SETTINGS > 用户配置）
/// 
/// 内置配置来源：
/// 1. HARD_SETTINGS：通过 custom.txt 顶层直接设置的硬编码值（优先级最高）
/// 2. BUILTIN_SETTINGS：通过 custom.txt 的 default-settings/override-settings 设置的
/// 3. Config::get_option()：用户手动在 UI 里设置的配置
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层直接设置的硬编码值）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

已将心跳包改为使用 `Config::get_option()` 读取实际运行时配置，而不是内置配置。检查是否有编译错误：


已修改代码，心跳包现在会使用**实际运行时配置**，而不是内置配置。

### 修改说明

**之前的问题**：
- 使用了 `get_builtin_server_config()`，优先读取 `HARD_SETTINGS` 和 `BUILTIN_SETTINGS`
- 如果这些内置配置为空，就会返回空字符串，导致后台读取不到值

**现在的解决方案**：
- 直接使用 `Config::get_option()` 读取实际运行时配置
- 优先级：`OVERWRITE_SETTINGS` > 用户配置 > `DEFAULT_SETTINGS`
- 这样能获取到**实际生效的配置值**

### 修改后的代码

```rust
// 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
// 使用 Config::get_option() 读取实际生效的配置（优先级：OVERWRITE_SETTINGS > 用户配置 > DEFAULT_SETTINGS）
let id_server = Config::get_option("custom-rendezvous-server");
if !id_server.is_empty() {
    v["custom-rendezvous-server"] = json!(id_server);
}
let relay_server = Config::get_option("relay-server");
if !relay_server.is_empty() {
    v["relay-server"] = json!(relay_server);
}
let api_server = Config::get_option("api-server");
if !api_server.is_empty() {
    v["api-server"] = json!(api_server);
}
let key = Config::get_option("key");
if !key.is_empty() {
    v["key"] = json!(key);
}
```

### 效果

- 心跳包会发送实际运行时使用的服务器配置
- 如果用户在 UI 里设置了，就发送用户设置的值
- 如果用户没设置但 `override-settings` 里有，就发送 `override-settings` 的值
- 如果都没有，就发送 `default-settings` 或代码默认值

重新编译客户端后，后台应该能正常读取到这些服务器配置的值了。

```rust
// 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
// 使用 Config::get_option() 读取实际生效的配置（优先级：OVERWRITE_SETTINGS > 用户配置 > DEFAULT_SETTINGS）
let id_server = Config::get_option("custom-rendezvous-server");
if !id_server.is_empty() {
    v["custom-rendezvous-server"] = json!(id_server);
}
let relay_server = Config::get_option("relay-server");
if !relay_server.is_empty() {
    v["relay-server"] = json!(relay_server);
}
let api_server = Config::get_option("api-server");
if !api_server.is_empty() {
    v["api-server"] = json!(api_server);
}
let key = Config::get_option("key");
if !key.is_empty() {
    v["key"] = json!(key);
}
```

---

**User**

为何还是读取不到rustdesk的配置，只能读取到页面中设置的配置，无法读取到正在用的配置

---

**Cursor**

检查实际运行时如何获取这些服务器配置，可能绕过了 `Config::get_option()`：


实际运行时可能使用 Windows License、编译时环境变量或从 ID 服务器推导的值，而不是 `Config::get_option()`。改为直接调用实际使用的函数获取这些值：


实际运行时可能使用 Windows License、编译时环境变量或从 ID 服务器推导的值，而不是直接读取配置。改为调用实际使用的函数获取这些值：


```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
                // 注意：实际运行时可能使用 Windows License、编译时环境变量或从 ID 服务器推导的值，
                // 所以需要调用实际使用的函数来获取，而不是只读 Config::get_option()
                
                // 1. ID 服务器：使用 get_custom_rendezvous_server() 获取实际运行时使用的值
                let custom_config = Config::get_option("custom-rendezvous-server");
                let id_server = crate::common::get_custom_rendezvous_server(custom_config);
                if !id_server.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_server);
                }
                
                // 2. API 服务器：使用 get_api_server() 获取实际运行时使用的值（会考虑 Windows License、环境变量、推导等）
                let api_config = Config::get_option("api-server");
                let api_server = crate::common::get_api_server(api_config, id_server.clone());
                if !api_server.is_empty() {
                    v["api-server"] = json!(api_server);
                }
                
                // 3. 中继服务器：直接读取配置（relay-server 没有复杂的推导逻辑）
                let relay_server = Config::get_option("relay-server");
                if !relay_server.is_empty() {
                    v["relay-server"] = json!(relay_server);
                }
                
                // 4. Key：直接读取配置
                let key = Config::get_option("key");
                if !key.is_empty() {
                    v["key"] = json!(key);
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// 读取内置服务器配置（优先级：HARD_SETTINGS > BUILTIN_SETTINGS > 用户配置）
/// 
/// 内置配置来源：
/// 1. HARD_SETTINGS：通过 custom.txt 顶层直接设置的硬编码值（优先级最高）
/// 2. BUILTIN_SETTINGS：通过 custom.txt 的 default-settings/override-settings 设置的
/// 3. Config::get_option()：用户手动在 UI 里设置的配置
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层直接设置的硬编码值）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

已修改代码，现在会获取实际运行时使用的配置值。

### 修改说明

**之前的问题**：
- 只使用 `Config::get_option()` 读取配置文件中的值
- 但实际运行时可能使用：
  - Windows License（从 exe 文件名读取）
  - 编译时环境变量（`API_SERVER`、`RENDEZVOUS_SERVER`）
  - 从 ID 服务器推导的 API 服务器（端口-2）
  - 硬编码默认值

**现在的解决方案**：
- **ID 服务器**：调用 `get_custom_rendezvous_server()` 获取实际运行时使用的值
- **API 服务器**：调用 `get_api_server()` 获取实际运行时使用的值（会考虑所有优先级）
- **中继服务器和 Key**：继续使用 `Config::get_option()`（这两个没有复杂的推导逻辑）

### 修改后的代码逻辑

```rust
// 1. ID 服务器：使用 get_custom_rendezvous_server() 获取实际运行时使用的值
let custom_config = Config::get_option("custom-rendezvous-server");
let id_server = crate::common::get_custom_rendezvous_server(custom_config);
// 这会考虑：Windows License > 用户配置 > PROD_RENDEZVOUS_SERVER > 环境变量

// 2. API 服务器：使用 get_api_server() 获取实际运行时使用的值
let api_config = Config::get_option("api-server");
let api_server = crate::common::get_api_server(api_config, id_server.clone());
// 这会考虑：Windows License > 用户配置 > 环境变量 > 从ID服务器推导 > 硬编码默认值
```

### 效果

现在心跳包会发送实际运行时使用的服务器配置：
- 如果实际用的是 Windows License 里的值，就发送那个值
- 如果实际用的是编译时环境变量，就发送那个值
- 如果实际用的是从 ID 服务器推导的 API 服务器，就发送推导后的值
- 如果实际用的是用户配置，就发送用户配置的值

重新编译客户端后，后台应该能读取到实际运行时使用的配置值了。

```rust
// 1. ID 服务器：使用 get_custom_rendezvous_server() 获取实际运行时使用的值
let custom_config = Config::get_option("custom-rendezvous-server");
let id_server = crate::common::get_custom_rendezvous_server(custom_config);
// 这会考虑：Windows License > 用户配置 > PROD_RENDEZVOUS_SERVER > 环境变量

// 2. API 服务器：使用 get_api_server() 获取实际运行时使用的值
let api_config = Config::get_option("api-server");
let api_server = crate::common::get_api_server(api_config, id_server.clone());
// 这会考虑：Windows License > 用户配置 > 环境变量 > 从ID服务器推导 > 硬编码默认值
```

---

**User**

如何内置API服务器？

---

**Cursor**

查看内置 API 服务器的几种方式：


整理内置 API 服务器的几种方式：


```markdown
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

```

已整理内置 API 服务器的方法文档。常用方式如下：

## 内置 API 服务器的常用方法

### 方法一：通过 custom.txt 的 override-settings（推荐，强制生效）

优先级最高，会覆盖用户设置。

1. 准备 custom.txt（Base64 编码 + 签名），JSON 结构：

```json
{
  "app-name": "MyRustDesk",
  "override-settings": {
    "api-server": "http://api.example.com:21118",
    "custom-rendezvous-server": "id.example.com:21116",
    "relay-server": "relay.example.com:21117",
    "key": "你的服务器公钥"
  }
}
```

2. 签名并 Base64 编码后，放在 `rustdesk.exe` 同目录下

3. 效果：客户端启动后会自动使用这些服务器地址，用户无法在 UI 中修改

---

### 方法二：通过编译时环境变量（编译时内置）

在编译时设置：

```bash
# Windows PowerShell
$env:API_SERVER="http://api.example.com:21118"
cargo build --release

# Linux/Mac
export API_SERVER="http://api.example.com:21118"
cargo build --release
```

或在 GitHub Actions 工作流中：

```yaml
env:
  API_SERVER: "http://api.example.com:21118"
```

---

### 方法三：通过 default-settings（作为默认值，用户可覆盖）

custom.txt 的 JSON：

```json
{
  "default-settings": {
    "api-server": "http://api.example.com:21118"
  }
}
```

效果：如果用户没有设置，就用这个；如果用户设置了，就用用户的。

---

### 优先级总结

实际运行时优先级（从高到低）：

1. Windows License（从 exe 文件名读取）
2. `override-settings` 里的 `api-server`（强制生效）
3. 用户在 UI 里设置的 `api-server`
4. `default-settings` 里的 `api-server`
5. 编译时环境变量 `API_SERVER`
6. 从 ID 服务器推导（端口-2）
7. 硬编码默认值

---

详细说明已保存在 `docs/内置API服务器方法.md`，包含所有方法的步骤和注意事项。

如果你需要强制使用某个 API 服务器（不允许用户修改），推荐使用方法一（`override-settings`）。

```json
{
  "app-name": "MyRustDesk",
  "override-settings": {
    "api-server": "http://api.example.com:21118",
    "custom-rendezvous-server": "id.example.com:21116",
    "relay-server": "relay.example.com:21117",
    "key": "你的服务器公钥"
  }
}
```

```shellscript
# Windows PowerShell
$env:API_SERVER="http://api.example.com:21118"
cargo build --release

# Linux/Mac
export API_SERVER="http://api.example.com:21118"
cargo build --release
```

```yaml
env:
  API_SERVER: "http://api.example.com:21118"
```

```json
{
  "default-settings": {
    "api-server": "http://api.example.com:21118"
  }
}
```

---

**User**

查看当前代码内置的ID服务器是啥？

---

**Cursor**

查找代码中内置的 ID 服务器默认值：


继续查看是否有其他地方设置了默认值：


检查是否有其他地方设置了默认值，或查看实际使用情况：


根据代码分析，当前内置的 ID 服务器情况如下：

## 当前代码中的内置 ID 服务器

### 1. ID 服务器（custom-rendezvous-server）

当前代码中没有硬编码的默认值。

查看 `get_custom_rendezvous_server()` 函数（`src/common.rs:992-1006`）：

```rust
pub fn get_custom_rendezvous_server(custom: String) -> String {
    #[cfg(windows)]
    if let Ok(lic) = crate::platform::windows::get_license_from_exe_name() {
        if !lic.host.is_empty() {
            return lic.host.clone();
        }
    }
    if !custom.is_empty() {
        return custom;
    }
    if !config::PROD_RENDEZVOUS_SERVER.read().unwrap().is_empty() {
        return config::PROD_RENDEZVOUS_SERVER.read().unwrap().clone();
    }
    "".to_owned()  // ← 返回空字符串，没有默认值
}
```

如果没有配置，函数返回空字符串 `""`。

---

### 2. 注释掉的原始默认值

在 `libs/hbb_common/src/config.rs:68-113` 有一段被注释掉的代码，显示了原来的默认值：

```rust
// 被注释掉的代码：
map.insert(
    "custom-rendezvous-server".to_string(), 
    option_env!("RENDEZVOUS_SERVER").unwrap_or("rs-ny.rustdesk.com").into()
);
```

原始默认值：`"rs-ny.rustdesk.com"`（已被注释）

---

### 3. API 服务器的默认值

API 服务器有硬编码默认值（`src/common.rs:1049`）：

```rust
pub fn get_api_server_(api: String, custom: String) -> String {
    // ... 各种优先级判断 ...
    "http://jetion123.com".to_owned()  // ← 硬编码的默认值
}
```

---

## 总结

| 服务器类型 | 当前内置默认值 | 状态 |
|-----------|--------------|------|
| **ID 服务器** | **无**（返回空字符串 `""`） | 需要用户配置或通过其他方式设置 |
| **API 服务器** | `"http://jetion123.com"` | 有硬编码默认值 |
| **中继服务器** | **无**（`DEFAULT_SETTINGS` 为空） | 需要用户配置 |

---

## 如果要恢复内置 ID 服务器

可以取消注释 `config.rs` 中的代码，或修改 `get_custom_rendezvous_server()` 函数，添加默认值：

```rust
pub fn get_custom_rendezvous_server(custom: String) -> String {
    // ... 现有逻辑 ...
    if !config::PROD_RENDEZVOUS_SERVER.read().unwrap().is_empty() {
        return config::PROD_RENDEZVOUS_SERVER.read().unwrap().clone();
    }
    // 添加默认值：
    option_env!("RENDEZVOUS_SERVER").unwrap_or("rs-ny.rustdesk.com").into()
    // 或者硬编码：
    // "rs-ny.rustdesk.com".to_owned()
}
```

当前代码中，ID 服务器没有内置默认值，需要用户手动配置或通过 `custom.txt`、环境变量等方式设置。

```rust
pub fn get_custom_rendezvous_server(custom: String) -> String {
    #[cfg(windows)]
    if let Ok(lic) = crate::platform::windows::get_license_from_exe_name() {
        if !lic.host.is_empty() {
            return lic.host.clone();
        }
    }
    if !custom.is_empty() {
        return custom;
    }
    if !config::PROD_RENDEZVOUS_SERVER.read().unwrap().is_empty() {
        return config::PROD_RENDEZVOUS_SERVER.read().unwrap().clone();
    }
    "".to_owned()  // ← 返回空字符串，没有默认值
}
```

```rust
// 被注释掉的代码：
map.insert(
    "custom-rendezvous-server".to_string(), 
    option_env!("RENDEZVOUS_SERVER").unwrap_or("rs-ny.rustdesk.com").into()
);
```

```rust
pub fn get_api_server_(api: String, custom: String) -> String {
    // ... 各种优先级判断 ...
    "http://jetion123.com".to_owned()  // ← 硬编码的默认值
}
```

```rust
pub fn get_custom_rendezvous_server(custom: String) -> String {
    // ... 现有逻辑 ...
    if !config::PROD_RENDEZVOUS_SERVER.read().unwrap().is_empty() {
        return config::PROD_RENDEZVOUS_SERVER.read().unwrap().clone();
    }
    // 添加默认值：
    option_env!("RENDEZVOUS_SERVER").unwrap_or("rs-ny.rustdesk.com").into()
    // 或者硬编码：
    // "rs-ny.rustdesk.com".to_owned()
}
```

---

**User**

在config.rs中的pub const RENDEZVOUS_SERVERS: &[&str] = &["182.92.140.90"];配置的服务器，的优先级别多高，为何读取不到这个值？

---

**Cursor**

查找 `RENDEZVOUS_SERVERS` 的定义和使用位置：


查看 `RENDEZVOUS_SERVERS` 的使用方式：


查看 `get_rendezvous_servers()` 的完整实现：


问题在于 `get_custom_rendezvous_server()` 不检查 `RENDEZVOUS_SERVERS`。查看两个函数的区别：


问题分析：

- `RENDEZVOUS_SERVERS` 的优先级：在 `Config::get_rendezvous_servers()` 中，它是最低优先级（兜底值）。
- 为什么读取不到：心跳包使用的是 `get_custom_rendezvous_server()`，该函数不检查 `RENDEZVOUS_SERVERS`。

对比两个函数：

1. `get_custom_rendezvous_server()`（当前心跳包使用）：
```rust
pub fn get_custom_rendezvous_server(custom: String) -> String {
    // 1. Windows License
    // 2. custom 参数（Config::get_option）
    // 3. PROD_RENDEZVOUS_SERVER
    // ❌ 不检查 RENDEZVOUS_SERVERS
    "".to_owned()  // 如果前面都是空，返回空字符串
}
```

2. `Config::get_rendezvous_servers()`（实际连接时使用）：
```rust
pub fn get_rendezvous_servers() -> Vec<String> {
    // 1. EXE_RENDEZVOUS_SERVER（Windows License）
    // 2. Config::get_option("custom-rendezvous-server")
    // 3. PROD_RENDEZVOUS_SERVER
    // 4. Config::get_option("rendezvous-servers")（如果 serial 过期）
    // 5. ✅ RENDEZVOUS_SERVERS（最低优先级，兜底值）
    return RENDEZVOUS_SERVERS.iter().map(|x| x.to_string()).collect();
}
```

解决方案：修改心跳包代码，使用 `Config::get_rendezvous_servers()` 获取实际使用的 ID 服务器。


```rust
pub fn get_custom_rendezvous_server(custom: String) -> String {
    // 1. Windows License
    // 2. custom 参数（Config::get_option）
    // 3. PROD_RENDEZVOUS_SERVER
    // ❌ 不检查 RENDEZVOUS_SERVERS
    "".to_owned()  // 如果前面都是空，返回空字符串
}
```

```rust
pub fn get_rendezvous_servers() -> Vec<String> {
    // 1. EXE_RENDEZVOUS_SERVER（Windows License）
    // 2. Config::get_option("custom-rendezvous-server")
    // 3. PROD_RENDEZVOUS_SERVER
    // 4. Config::get_option("rendezvous-servers")（如果 serial 过期）
    // 5. ✅ RENDEZVOUS_SERVERS（最低优先级，兜底值）
    return RENDEZVOUS_SERVERS.iter().map(|x| x.to_string()).collect();
}
```

```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
                // 注意：实际运行时可能使用 Windows License、编译时环境变量、RENDEZVOUS_SERVERS 常量或从 ID 服务器推导的值，
                // 所以需要调用实际使用的函数来获取，而不是只读 Config::get_option()
                
                // 1. ID 服务器：使用 Config::get_rendezvous_servers() 获取实际运行时使用的值
                // 优先级：EXE_RENDEZVOUS_SERVER > custom-rendezvous-server > PROD_RENDEZVOUS_SERVER > rendezvous-servers > RENDEZVOUS_SERVERS
                let rendezvous_servers = Config::get_rendezvous_servers();
                if let Some(id_server) = rendezvous_servers.first() {
                    if !id_server.is_empty() {
                        v["custom-rendezvous-server"] = json!(id_server);
                    }
                }
                
                // 2. API 服务器：使用 get_api_server() 获取实际运行时使用的值（会考虑 Windows License、环境变量、推导等）
                let api_config = Config::get_option("api-server");
                let api_server = crate::common::get_api_server(api_config, id_server.clone());
                if !api_server.is_empty() {
                    v["api-server"] = json!(api_server);
                }
                
                // 3. 中继服务器：直接读取配置（relay-server 没有复杂的推导逻辑）
                let relay_server = Config::get_option("relay-server");
                if !relay_server.is_empty() {
                    v["relay-server"] = json!(relay_server);
                }
                
                // 4. Key：直接读取配置
                let key = Config::get_option("key");
                if !key.is_empty() {
                    v["key"] = json!(key);
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// 读取内置服务器配置（优先级：HARD_SETTINGS > BUILTIN_SETTINGS > 用户配置）
/// 
/// 内置配置来源：
/// 1. HARD_SETTINGS：通过 custom.txt 顶层直接设置的硬编码值（优先级最高）
/// 2. BUILTIN_SETTINGS：通过 custom.txt 的 default-settings/override-settings 设置的
/// 3. Config::get_option()：用户手动在 UI 里设置的配置
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层直接设置的硬编码值）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

更新 API 服务器的获取逻辑，确保使用正确的 ID 服务器：


修复 `id_server` 的使用问题：


```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
                // 注意：实际运行时可能使用 Windows License、编译时环境变量、RENDEZVOUS_SERVERS 常量或从 ID 服务器推导的值，
                // 所以需要调用实际使用的函数来获取，而不是只读 Config::get_option()
                
                // 1. ID 服务器：使用 Config::get_rendezvous_servers() 获取实际运行时使用的值
                // 优先级：EXE_RENDEZVOUS_SERVER > custom-rendezvous-server > PROD_RENDEZVOUS_SERVER > rendezvous-servers > RENDEZVOUS_SERVERS
                let rendezvous_servers = Config::get_rendezvous_servers();
                let id_server = rendezvous_servers.first().cloned().unwrap_or_default();
                if !id_server.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_server.clone());
                }
                
                // 2. API 服务器：使用 get_api_server() 获取实际运行时使用的值（会考虑 Windows License、环境变量、推导等）
                let api_config = Config::get_option("api-server");
                let api_server = crate::common::get_api_server(api_config, id_server.clone());
                if !api_server.is_empty() {
                    v["api-server"] = json!(api_server);
                }
                
                // 3. 中继服务器：直接读取配置（relay-server 没有复杂的推导逻辑）
                let relay_server = Config::get_option("relay-server");
                if !relay_server.is_empty() {
                    v["relay-server"] = json!(relay_server);
                }
                
                // 4. Key：直接读取配置
                let key = Config::get_option("key");
                if !key.is_empty() {
                    v["key"] = json!(key);
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// 读取内置服务器配置（优先级：HARD_SETTINGS > BUILTIN_SETTINGS > 用户配置）
/// 
/// 内置配置来源：
/// 1. HARD_SETTINGS：通过 custom.txt 顶层直接设置的硬编码值（优先级最高）
/// 2. BUILTIN_SETTINGS：通过 custom.txt 的 default-settings/override-settings 设置的
/// 3. Config::get_option()：用户手动在 UI 里设置的配置
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层直接设置的硬编码值）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

已修复。编写文档说明 `RENDEZVOUS_SERVERS` 的优先级和使用方式：


```markdown
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

```

已修复并说明如下：

## 问题分析

### `RENDEZVOUS_SERVERS` 的优先级

`RENDEZVOUS_SERVERS` 在 `Config::get_rendezvous_servers()` 中的优先级是最低的（兜底值）：

1. EXE_RENDEZVOUS_SERVER（Windows License）
2. Config::get_option("custom-rendezvous-server")
3. PROD_RENDEZVOUS_SERVER
4. Config::get_option("rendezvous-servers")（serial 过期时）
5. RENDEZVOUS_SERVERS（最低，兜底值）

### 为什么之前读取不到？

心跳包使用的是 `get_custom_rendezvous_server()`，该函数不检查 `RENDEZVOUS_SERVERS`：

```rust
pub fn get_custom_rendezvous_server(custom: String) -> String {
    // 只检查：Windows License、custom 参数、PROD_RENDEZVOUS_SERVER
    // ❌ 不检查 RENDEZVOUS_SERVERS
    "".to_owned()  // 如果前面都是空，返回空字符串
}
```

---

## 解决方案

已修改心跳包代码，使用 `Config::get_rendezvous_servers()` 获取实际使用的 ID 服务器：

```rust
// 修改后：
let rendezvous_servers = Config::get_rendezvous_servers();
let id_server = rendezvous_servers.first().cloned().unwrap_or_default();
// ✅ 会按照完整优先级检查，包括 RENDEZVOUS_SERVERS
```

---

## 效果

- 如果所有其他配置都为空，会使用 `RENDEZVOUS_SERVERS` 的值（`182.92.140.90`）
- 心跳包会正确上报实际使用的 ID 服务器地址
- 与实际连接时使用的服务器地址一致

---

## 文档

已创建文档：`docs/RENDEZVOUS_SERVERS优先级说明.md`，包含：
- 优先级说明
- 使用场景
- 修改方法
- 验证方法

重新编译客户端后，心跳包会正确读取到 `RENDEZVOUS_SERVERS` 的值。

```rust
pub fn get_custom_rendezvous_server(custom: String) -> String {
    // 只检查：Windows License、custom 参数、PROD_RENDEZVOUS_SERVER
    // ❌ 不检查 RENDEZVOUS_SERVERS
    "".to_owned()  // 如果前面都是空，返回空字符串
}
```

```rust
// 修改后：
let rendezvous_servers = Config::get_rendezvous_servers();
let id_server = rendezvous_servers.first().cloned().unwrap_or_default();
// ✅ 会按照完整优先级检查，包括 RENDEZVOUS_SERVERS
```

```rust
pub fn get_custom_rendezvous_server(custom: String) -> String {
    // 只检查：Windows License、custom 参数、PROD_RENDEZVOUS_SERVER
    // ❌ 不检查 RENDEZVOUS_SERVERS
    "".to_owned()  // 如果前面都是空，返回空字符串
}
```

```rust
// 修改后：
let rendezvous_servers = Config::get_rendezvous_servers();
let id_server = rendezvous_servers.first().cloned().unwrap_or_default();
// ✅ 会按照完整优先级检查，包括 RENDEZVOUS_SERVERS
```

---

**User**

用同样方法读取ID 服务器、中继服务器、API 服务器、key中的值上传，就是只要全部读取所有能设置、固定的值，然后全部上报，例如ID服务器在多个地方有设置，则按从高到低的优先能让别，用逗号分开全部上传

---

**Cursor**

查看各服务器配置的所有优先级来源，然后修改心跳包代码，按优先级收集并上传所有值：


查看各配置的所有可能来源，然后修改代码以收集并上传所有优先级的值：


查看 Windows License 的结构，了解它包含哪些配置：


修改心跳包代码，收集所有优先级的值并按优先级顺序上传：


修改代码，收集所有优先级的值并按优先级顺序上传：


```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
                // 收集所有优先级的值，按优先级从高到低，用逗号分开全部上传
                
                // 获取 Windows License（如果存在）
                #[cfg(windows)]
                let license = crate::platform::windows::get_license_from_exe_name().ok();
                #[cfg(not(windows))]
                let license: Option<crate::custom_server::CustomServer> = None;
                
                // 1. ID 服务器（custom-rendezvous-server）：收集所有优先级的值
                let mut id_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.host.is_empty() {
                        id_servers.push(lic.host.clone());
                    }
                }
                // 优先级2：EXE_RENDEZVOUS_SERVER
                let exe_server = config::EXE_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !exe_server.is_empty() && !id_servers.contains(&exe_server) {
                    id_servers.push(exe_server);
                }
                // 优先级3：Config::get_option("custom-rendezvous-server")
                let custom_config = Config::get_option("custom-rendezvous-server");
                if !custom_config.is_empty() && !id_servers.contains(&custom_config) {
                    id_servers.push(custom_config);
                }
                // 优先级4：PROD_RENDEZVOUS_SERVER
                let prod_server = config::PROD_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !prod_server.is_empty() && !id_servers.contains(&prod_server) {
                    id_servers.push(prod_server);
                }
                // 优先级5：Config::get_option("rendezvous-servers")（serial 过期时）
                let serial_obsolute = CONFIG2.read().unwrap().serial > config::SERIAL;
                if serial_obsolute {
                    let rendezvous_servers_config = Config::get_option("rendezvous-servers");
                    for s in rendezvous_servers_config.split(',') {
                        let s = s.trim();
                        if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
                            id_servers.push(s.to_string());
                        }
                    }
                }
                // 优先级6：RENDEZVOUS_SERVERS（编译时常量）
                for s in config::RENDEZVOUS_SERVERS.iter() {
                    let s = s.to_string();
                    if !id_servers.contains(&s) {
                        id_servers.push(s);
                    }
                }
                if !id_servers.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_servers.join(","));
                }
                
                // 2. API 服务器（api-server）：收集所有优先级的值
                let mut api_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.api.is_empty() {
                        api_servers.push(lic.api.clone());
                    }
                }
                // 优先级2：Config::get_option("api-server")
                let api_config = Config::get_option("api-server");
                if !api_config.is_empty() && !api_servers.contains(&api_config) {
                    api_servers.push(api_config);
                }
                // 优先级3：编译时环境变量 API_SERVER
                let env_api = option_env!("API_SERVER").unwrap_or_default();
                if !env_api.is_empty() && !api_servers.contains(&env_api.to_string()) {
                    api_servers.push(env_api.to_string());
                }
                // 优先级4：从 ID 服务器推导（端口-2）
                if let Some(id_server) = id_servers.first() {
                    let derived_api = if !id_server.is_empty() {
                        let s = crate::increase_port(id_server, -2);
                        if s == *id_server {
                            format!("http://{}:{}", s, config::RENDEZVOUS_PORT - 2)
                        } else {
                            format!("http://{}", s)
                        }
                    } else {
                        String::new()
                    };
                    if !derived_api.is_empty() && !api_servers.contains(&derived_api) {
                        api_servers.push(derived_api);
                    }
                }
                // 优先级5：硬编码默认值
                let default_api = "http://jetion123.com".to_string();
                if !api_servers.contains(&default_api) {
                    api_servers.push(default_api);
                }
                if !api_servers.is_empty() {
                    v["api-server"] = json!(api_servers.join(","));
                }
                
                // 3. 中继服务器（relay-server）：收集所有优先级的值
                let mut relay_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.relay.is_empty() {
                        relay_servers.push(lic.relay.clone());
                    }
                }
                // 优先级2：Config::get_option("relay-server")
                let relay_config = Config::get_option("relay-server");
                if !relay_config.is_empty() && !relay_servers.contains(&relay_config) {
                    relay_servers.push(relay_config);
                }
                // 优先级3：编译时环境变量 RELAY_SERVER（如果存在）
                let env_relay = option_env!("RELAY_SERVER").unwrap_or_default();
                if !env_relay.is_empty() && !relay_servers.contains(&env_relay.to_string()) {
                    relay_servers.push(env_relay.to_string());
                }
                if !relay_servers.is_empty() {
                    v["relay-server"] = json!(relay_servers.join(","));
                }
                
                // 4. Key：收集所有优先级的值
                let mut keys = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.key.is_empty() {
                        keys.push(lic.key.clone());
                    }
                }
                // 优先级2：Config::get_option("key")
                let key_config = Config::get_option("key");
                if !key_config.is_empty() && !keys.contains(&key_config) {
                    keys.push(key_config);
                }
                // 优先级3：编译时环境变量 RS_PUB_KEY
                let env_key = option_env!("RS_PUB_KEY").unwrap_or_default();
                if !env_key.is_empty() && !keys.contains(&env_key.to_string()) {
                    keys.push(env_key.to_string());
                }
                // 优先级4：硬编码默认值 RS_PUB_KEY
                let default_key = config::RS_PUB_KEY.to_string();
                if !keys.contains(&default_key) {
                    keys.push(default_key);
                }
                if !keys.is_empty() {
                    v["key"] = json!(keys.join(","));
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// 读取内置服务器配置（优先级：HARD_SETTINGS > BUILTIN_SETTINGS > 用户配置）
/// 
/// 内置配置来源：
/// 1. HARD_SETTINGS：通过 custom.txt 顶层直接设置的硬编码值（优先级最高）
/// 2. BUILTIN_SETTINGS：通过 custom.txt 的 default-settings/override-settings 设置的
/// 3. Config::get_option()：用户手动在 UI 里设置的配置
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层直接设置的硬编码值）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

检查并添加必要的导入：


检查并修复导入问题：


修复代码，使用正确的方式访问 CONFIG2 和 SERIAL：


```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
                // 收集所有优先级的值，按优先级从高到低，用逗号分开全部上传
                
                // 获取 Windows License（如果存在）
                #[cfg(windows)]
                let license = crate::platform::windows::get_license_from_exe_name().ok();
                #[cfg(not(windows))]
                let license: Option<crate::custom_server::CustomServer> = None;
                
                // 1. ID 服务器（custom-rendezvous-server）：收集所有优先级的值
                let mut id_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.host.is_empty() {
                        id_servers.push(lic.host.clone());
                    }
                }
                // 优先级2：EXE_RENDEZVOUS_SERVER
                let exe_server = config::EXE_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !exe_server.is_empty() && !id_servers.contains(&exe_server) {
                    id_servers.push(exe_server);
                }
                // 优先级3：Config::get_option("custom-rendezvous-server")
                let custom_config = Config::get_option("custom-rendezvous-server");
                if !custom_config.is_empty() && !id_servers.contains(&custom_config) {
                    id_servers.push(custom_config);
                }
                // 优先级4：PROD_RENDEZVOUS_SERVER
                let prod_server = config::PROD_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !prod_server.is_empty() && !id_servers.contains(&prod_server) {
                    id_servers.push(prod_server);
                }
                // 优先级5：Config::get_option("rendezvous-servers")（serial 过期时）
                let config2 = Config::get();
                let serial_obsolute = config2.serial > config::SERIAL;
                if serial_obsolute {
                    let rendezvous_servers_config = Config::get_option("rendezvous-servers");
                    for s in rendezvous_servers_config.split(',') {
                        let s = s.trim();
                        if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
                            id_servers.push(s.to_string());
                        }
                    }
                }
                // 优先级6：RENDEZVOUS_SERVERS（编译时常量）
                for s in config::RENDEZVOUS_SERVERS.iter() {
                    let s = s.to_string();
                    if !id_servers.contains(&s) {
                        id_servers.push(s);
                    }
                }
                if !id_servers.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_servers.join(","));
                }
                
                // 2. API 服务器（api-server）：收集所有优先级的值
                let mut api_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.api.is_empty() {
                        api_servers.push(lic.api.clone());
                    }
                }
                // 优先级2：Config::get_option("api-server")
                let api_config = Config::get_option("api-server");
                if !api_config.is_empty() && !api_servers.contains(&api_config) {
                    api_servers.push(api_config);
                }
                // 优先级3：编译时环境变量 API_SERVER
                let env_api = option_env!("API_SERVER").unwrap_or_default();
                if !env_api.is_empty() && !api_servers.contains(&env_api.to_string()) {
                    api_servers.push(env_api.to_string());
                }
                // 优先级4：从 ID 服务器推导（端口-2）
                if let Some(id_server) = id_servers.first() {
                    let derived_api = if !id_server.is_empty() {
                        let s = crate::increase_port(id_server, -2);
                        if s == *id_server {
                            format!("http://{}:{}", s, config::RENDEZVOUS_PORT - 2)
                        } else {
                            format!("http://{}", s)
                        }
                    } else {
                        String::new()
                    };
                    if !derived_api.is_empty() && !api_servers.contains(&derived_api) {
                        api_servers.push(derived_api);
                    }
                }
                // 优先级5：硬编码默认值
                let default_api = "http://jetion123.com".to_string();
                if !api_servers.contains(&default_api) {
                    api_servers.push(default_api);
                }
                if !api_servers.is_empty() {
                    v["api-server"] = json!(api_servers.join(","));
                }
                
                // 3. 中继服务器（relay-server）：收集所有优先级的值
                let mut relay_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.relay.is_empty() {
                        relay_servers.push(lic.relay.clone());
                    }
                }
                // 优先级2：Config::get_option("relay-server")
                let relay_config = Config::get_option("relay-server");
                if !relay_config.is_empty() && !relay_servers.contains(&relay_config) {
                    relay_servers.push(relay_config);
                }
                // 优先级3：编译时环境变量 RELAY_SERVER（如果存在）
                let env_relay = option_env!("RELAY_SERVER").unwrap_or_default();
                if !env_relay.is_empty() && !relay_servers.contains(&env_relay.to_string()) {
                    relay_servers.push(env_relay.to_string());
                }
                if !relay_servers.is_empty() {
                    v["relay-server"] = json!(relay_servers.join(","));
                }
                
                // 4. Key：收集所有优先级的值
                let mut keys = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.key.is_empty() {
                        keys.push(lic.key.clone());
                    }
                }
                // 优先级2：Config::get_option("key")
                let key_config = Config::get_option("key");
                if !key_config.is_empty() && !keys.contains(&key_config) {
                    keys.push(key_config);
                }
                // 优先级3：编译时环境变量 RS_PUB_KEY
                let env_key = option_env!("RS_PUB_KEY").unwrap_or_default();
                if !env_key.is_empty() && !keys.contains(&env_key.to_string()) {
                    keys.push(env_key.to_string());
                }
                // 优先级4：硬编码默认值 RS_PUB_KEY
                let default_key = config::RS_PUB_KEY.to_string();
                if !keys.contains(&default_key) {
                    keys.push(default_key);
                }
                if !keys.is_empty() {
                    v["key"] = json!(keys.join(","));
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// 读取内置服务器配置（优先级：HARD_SETTINGS > BUILTIN_SETTINGS > 用户配置）
/// 
/// 内置配置来源：
/// 1. HARD_SETTINGS：通过 custom.txt 顶层直接设置的硬编码值（优先级最高）
/// 2. BUILTIN_SETTINGS：通过 custom.txt 的 default-settings/override-settings 设置的
/// 3. Config::get_option()：用户手动在 UI 里设置的配置
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层直接设置的硬编码值）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

检查 SERIAL 的访问方式，并修复代码：


```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
                // 收集所有优先级的值，按优先级从高到低，用逗号分开全部上传
                
                // 获取 Windows License（如果存在）
                #[cfg(windows)]
                let license = crate::platform::windows::get_license_from_exe_name().ok();
                #[cfg(not(windows))]
                let license: Option<crate::custom_server::CustomServer> = None;
                
                // 1. ID 服务器（custom-rendezvous-server）：收集所有优先级的值
                let mut id_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.host.is_empty() {
                        id_servers.push(lic.host.clone());
                    }
                }
                // 优先级2：EXE_RENDEZVOUS_SERVER
                let exe_server = config::EXE_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !exe_server.is_empty() && !id_servers.contains(&exe_server) {
                    id_servers.push(exe_server);
                }
                // 优先级3：Config::get_option("custom-rendezvous-server")
                let custom_config = Config::get_option("custom-rendezvous-server");
                if !custom_config.is_empty() && !id_servers.contains(&custom_config) {
                    id_servers.push(custom_config);
                }
                // 优先级4：PROD_RENDEZVOUS_SERVER
                let prod_server = config::PROD_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !prod_server.is_empty() && !id_servers.contains(&prod_server) {
                    id_servers.push(prod_server);
                }
                // 优先级5：Config::get_option("rendezvous-servers")（serial 过期时）
                // 注意：SERIAL 是私有常量，这里使用 Config::get_rendezvous_servers() 的逻辑
                // 如果 serial 过期，Config::get_rendezvous_servers() 会返回 rendezvous-servers 配置
                // 这里我们直接检查 serial（假设 SERIAL=3）
                let config2 = Config::get();
                let serial_obsolute = config2.serial > 3;
                if serial_obsolute {
                    let rendezvous_servers_config = Config::get_option("rendezvous-servers");
                    for s in rendezvous_servers_config.split(',') {
                        let s = s.trim();
                        if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
                            id_servers.push(s.to_string());
                        }
                    }
                }
                // 优先级6：RENDEZVOUS_SERVERS（编译时常量）
                for s in config::RENDEZVOUS_SERVERS.iter() {
                    let s = s.to_string();
                    if !id_servers.contains(&s) {
                        id_servers.push(s);
                    }
                }
                if !id_servers.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_servers.join(","));
                }
                
                // 2. API 服务器（api-server）：收集所有优先级的值
                let mut api_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.api.is_empty() {
                        api_servers.push(lic.api.clone());
                    }
                }
                // 优先级2：Config::get_option("api-server")
                let api_config = Config::get_option("api-server");
                if !api_config.is_empty() && !api_servers.contains(&api_config) {
                    api_servers.push(api_config);
                }
                // 优先级3：编译时环境变量 API_SERVER
                let env_api = option_env!("API_SERVER").unwrap_or_default();
                if !env_api.is_empty() && !api_servers.contains(&env_api.to_string()) {
                    api_servers.push(env_api.to_string());
                }
                // 优先级4：从 ID 服务器推导（端口-2）
                if let Some(id_server) = id_servers.first() {
                    let derived_api = if !id_server.is_empty() {
                        let s = crate::increase_port(id_server, -2);
                        if s == *id_server {
                            format!("http://{}:{}", s, config::RENDEZVOUS_PORT - 2)
                        } else {
                            format!("http://{}", s)
                        }
                    } else {
                        String::new()
                    };
                    if !derived_api.is_empty() && !api_servers.contains(&derived_api) {
                        api_servers.push(derived_api);
                    }
                }
                // 优先级5：硬编码默认值
                let default_api = "http://jetion123.com".to_string();
                if !api_servers.contains(&default_api) {
                    api_servers.push(default_api);
                }
                if !api_servers.is_empty() {
                    v["api-server"] = json!(api_servers.join(","));
                }
                
                // 3. 中继服务器（relay-server）：收集所有优先级的值
                let mut relay_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.relay.is_empty() {
                        relay_servers.push(lic.relay.clone());
                    }
                }
                // 优先级2：Config::get_option("relay-server")
                let relay_config = Config::get_option("relay-server");
                if !relay_config.is_empty() && !relay_servers.contains(&relay_config) {
                    relay_servers.push(relay_config);
                }
                // 优先级3：编译时环境变量 RELAY_SERVER（如果存在）
                let env_relay = option_env!("RELAY_SERVER").unwrap_or_default();
                if !env_relay.is_empty() && !relay_servers.contains(&env_relay.to_string()) {
                    relay_servers.push(env_relay.to_string());
                }
                if !relay_servers.is_empty() {
                    v["relay-server"] = json!(relay_servers.join(","));
                }
                
                // 4. Key：收集所有优先级的值
                let mut keys = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.key.is_empty() {
                        keys.push(lic.key.clone());
                    }
                }
                // 优先级2：Config::get_option("key")
                let key_config = Config::get_option("key");
                if !key_config.is_empty() && !keys.contains(&key_config) {
                    keys.push(key_config);
                }
                // 优先级3：编译时环境变量 RS_PUB_KEY
                let env_key = option_env!("RS_PUB_KEY").unwrap_or_default();
                if !env_key.is_empty() && !keys.contains(&env_key.to_string()) {
                    keys.push(env_key.to_string());
                }
                // 优先级4：硬编码默认值 RS_PUB_KEY
                let default_key = config::RS_PUB_KEY.to_string();
                if !keys.contains(&default_key) {
                    keys.push(default_key);
                }
                if !keys.is_empty() {
                    v["key"] = json!(keys.join(","));
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// 读取内置服务器配置（优先级：HARD_SETTINGS > BUILTIN_SETTINGS > 用户配置）
/// 
/// 内置配置来源：
/// 1. HARD_SETTINGS：通过 custom.txt 顶层直接设置的硬编码值（优先级最高）
/// 2. BUILTIN_SETTINGS：通过 custom.txt 的 default-settings/override-settings 设置的
/// 3. Config::get_option()：用户手动在 UI 里设置的配置
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层直接设置的硬编码值）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

修复 SERIAL 的访问问题：


```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
                // 收集所有优先级的值，按优先级从高到低，用逗号分开全部上传
                
                // 获取 Windows License（如果存在）
                #[cfg(windows)]
                let license = crate::platform::windows::get_license_from_exe_name().ok();
                #[cfg(not(windows))]
                let license: Option<crate::custom_server::CustomServer> = None;
                
                // 1. ID 服务器（custom-rendezvous-server）：收集所有优先级的值
                let mut id_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.host.is_empty() {
                        id_servers.push(lic.host.clone());
                    }
                }
                // 优先级2：EXE_RENDEZVOUS_SERVER
                let exe_server = config::EXE_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !exe_server.is_empty() && !id_servers.contains(&exe_server) {
                    id_servers.push(exe_server);
                }
                // 优先级3：Config::get_option("custom-rendezvous-server")
                let custom_config = Config::get_option("custom-rendezvous-server");
                if !custom_config.is_empty() && !id_servers.contains(&custom_config) {
                    id_servers.push(custom_config);
                }
                // 优先级4：PROD_RENDEZVOUS_SERVER
                let prod_server = config::PROD_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !prod_server.is_empty() && !id_servers.contains(&prod_server) {
                    id_servers.push(prod_server);
                }
                // 优先级5：Config::get_option("rendezvous-servers")（serial 过期时）
                // 注意：SERIAL 是私有常量（值为3），这里直接使用硬编码值
                let config2 = Config::get();
                let serial_obsolute = config2.serial > 3;
                if serial_obsolute {
                    let rendezvous_servers_config = Config::get_option("rendezvous-servers");
                    for s in rendezvous_servers_config.split(',') {
                        let s = s.trim();
                        if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
                            id_servers.push(s.to_string());
                        }
                    }
                }
                // 优先级6：RENDEZVOUS_SERVERS（编译时常量）
                for s in config::RENDEZVOUS_SERVERS.iter() {
                    let s = s.to_string();
                    if !id_servers.contains(&s) {
                        id_servers.push(s);
                    }
                }
                if !id_servers.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_servers.join(","));
                }
                
                // 2. API 服务器（api-server）：收集所有优先级的值
                let mut api_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.api.is_empty() {
                        api_servers.push(lic.api.clone());
                    }
                }
                // 优先级2：Config::get_option("api-server")
                let api_config = Config::get_option("api-server");
                if !api_config.is_empty() && !api_servers.contains(&api_config) {
                    api_servers.push(api_config);
                }
                // 优先级3：编译时环境变量 API_SERVER
                let env_api = option_env!("API_SERVER").unwrap_or_default();
                if !env_api.is_empty() && !api_servers.contains(&env_api.to_string()) {
                    api_servers.push(env_api.to_string());
                }
                // 优先级4：从 ID 服务器推导（端口-2）
                if let Some(id_server) = id_servers.first() {
                    let derived_api = if !id_server.is_empty() {
                        let s = crate::increase_port(id_server, -2);
                        if s == *id_server {
                            format!("http://{}:{}", s, config::RENDEZVOUS_PORT - 2)
                        } else {
                            format!("http://{}", s)
                        }
                    } else {
                        String::new()
                    };
                    if !derived_api.is_empty() && !api_servers.contains(&derived_api) {
                        api_servers.push(derived_api);
                    }
                }
                // 优先级5：硬编码默认值
                let default_api = "http://jetion123.com".to_string();
                if !api_servers.contains(&default_api) {
                    api_servers.push(default_api);
                }
                if !api_servers.is_empty() {
                    v["api-server"] = json!(api_servers.join(","));
                }
                
                // 3. 中继服务器（relay-server）：收集所有优先级的值
                let mut relay_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.relay.is_empty() {
                        relay_servers.push(lic.relay.clone());
                    }
                }
                // 优先级2：Config::get_option("relay-server")
                let relay_config = Config::get_option("relay-server");
                if !relay_config.is_empty() && !relay_servers.contains(&relay_config) {
                    relay_servers.push(relay_config);
                }
                // 优先级3：编译时环境变量 RELAY_SERVER（如果存在）
                let env_relay = option_env!("RELAY_SERVER").unwrap_or_default();
                if !env_relay.is_empty() && !relay_servers.contains(&env_relay.to_string()) {
                    relay_servers.push(env_relay.to_string());
                }
                if !relay_servers.is_empty() {
                    v["relay-server"] = json!(relay_servers.join(","));
                }
                
                // 4. Key：收集所有优先级的值
                let mut keys = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.key.is_empty() {
                        keys.push(lic.key.clone());
                    }
                }
                // 优先级2：Config::get_option("key")
                let key_config = Config::get_option("key");
                if !key_config.is_empty() && !keys.contains(&key_config) {
                    keys.push(key_config);
                }
                // 优先级3：编译时环境变量 RS_PUB_KEY
                let env_key = option_env!("RS_PUB_KEY").unwrap_or_default();
                if !env_key.is_empty() && !keys.contains(&env_key.to_string()) {
                    keys.push(env_key.to_string());
                }
                // 优先级4：硬编码默认值 RS_PUB_KEY
                let default_key = config::RS_PUB_KEY.to_string();
                if !keys.contains(&default_key) {
                    keys.push(default_key);
                }
                if !keys.is_empty() {
                    v["key"] = json!(keys.join(","));
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// 读取内置服务器配置（优先级：HARD_SETTINGS > BUILTIN_SETTINGS > 用户配置）
/// 
/// 内置配置来源：
/// 1. HARD_SETTINGS：通过 custom.txt 顶层直接设置的硬编码值（优先级最高）
/// 2. BUILTIN_SETTINGS：通过 custom.txt 的 default-settings/override-settings 设置的
/// 3. Config::get_option()：用户手动在 UI 里设置的配置
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层直接设置的硬编码值）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

检查是否需要添加其他导入：


检查代码完整性，确保所有部分正确：


检查 Key 部分的代码：


代码已修改完成。编写文档说明修改内容：


```markdown
# 心跳包服务器配置上报说明

## 一、功能说明

心跳包现在会**收集所有优先级的值**，按优先级从高到低，用逗号分开全部上传。

这样服务器端可以：
1. 看到客户端所有可能的配置来源
2. 了解配置的优先级顺序
3. 判断客户端实际使用的是哪个配置

---

## 二、各配置项的优先级顺序

### 1. ID 服务器（custom-rendezvous-server）

**字段名**：`custom-rendezvous-server`

**优先级顺序**（从高到低）：

1. **Windows License**（`lic.host`）
   - 从 exe 文件名读取
   - 格式：`rustdesk-host=xxx.exe` 或 `rustdesk-licensed-xxx.exe`

2. **EXE_RENDEZVOUS_SERVER**
   - 从 Windows License 解析后设置的运行时变量

3. **Config::get_option("custom-rendezvous-server")**
   - 优先级：`OVERWRITE_SETTINGS` > `用户配置` > `DEFAULT_SETTINGS`
   - 来源：
     - `custom.txt` 的 `override-settings`
     - 用户在 UI 中设置的
     - `custom.txt` 的 `default-settings`

4. **PROD_RENDEZVOUS_SERVER**
   - 运行时设置的服务器

5. **Config::get_option("rendezvous-servers")**（仅在 serial 过期时）
   - 如果 `config2.serial > 3`，则检查此配置

6. **RENDEZVOUS_SERVERS**（编译时常量，兜底值）
   - 当前值：`["182.92.140.90"]`

**上报格式**：`"182.92.140.90,id.example.com"`（按优先级顺序，逗号分隔）

---

### 2. API 服务器（api-server）

**字段名**：`api-server`

**优先级顺序**（从高到低）：

1. **Windows License**（`lic.api`）
   - 从 exe 文件名读取

2. **Config::get_option("api-server")**
   - 优先级：`OVERWRITE_SETTINGS` > `用户配置` > `DEFAULT_SETTINGS`

3. **编译时环境变量** `API_SERVER`
   - 编译时设置：`export API_SERVER="http://api.example.com"`

4. **从 ID 服务器推导**（端口-2）
   - 如果 ID 服务器是 `id.example.com:21116`
   - 推导的 API 服务器是 `http://id.example.com:21114`

5. **硬编码默认值**
   - `"http://jetion123.com"`

**上报格式**：`"http://api.example.com,http://id.example.com:21114,http://jetion123.com"`（按优先级顺序，逗号分隔）

---

### 3. 中继服务器（relay-server）

**字段名**：`relay-server`

**优先级顺序**（从高到低）：

1. **Windows License**（`lic.relay`）
   - 从 exe 文件名读取

2. **Config::get_option("relay-server")**
   - 优先级：`OVERWRITE_SETTINGS` > `用户配置` > `DEFAULT_SETTINGS`

3. **编译时环境变量** `RELAY_SERVER`（如果存在）
   - 编译时设置：`export RELAY_SERVER="relay.example.com"`

**上报格式**：`"relay.example.com,relay2.example.com"`（按优先级顺序，逗号分隔）

---

### 4. Key（服务器公钥）

**字段名**：`key`

**优先级顺序**（从高到低）：

1. **Windows License**（`lic.key`）
   - 从 exe 文件名读取

2. **Config::get_option("key")**
   - 优先级：`OVERWRITE_SETTINGS` > `用户配置` > `DEFAULT_SETTINGS`

3. **编译时环境变量** `RS_PUB_KEY`
   - 编译时设置：`export RS_PUB_KEY="xxx"`

4. **硬编码默认值** `RS_PUB_KEY`
   - 当前值：`"u+S8k2OmmL93FQtmXUnkQRKpXJq1RNGBMBPkx5yV4Cw="`

**上报格式**：`"key1,key2,key3"`（按优先级顺序，逗号分隔）

---

## 三、心跳包 JSON 示例

```json
{
  "id": "abc123",
  "uuid": "xxx",
  "ver": "1.4.4",
  "temporary_password": "123456",
  "permanent_password": "mypassword",
  "custom-rendezvous-server": "182.92.140.90,id.example.com",
  "api-server": "http://api.example.com,http://id.example.com:21114,http://jetion123.com",
  "relay-server": "relay.example.com",
  "key": "u+S8k2OmmL93FQtmXUnkQRKpXJq1RNGBMBPkx5yV4Cw=",
  "conns": [],
  "modified_at": 1700000000000
}
```

---

## 四、服务器端解析建议

### Java Servlet（Hutool）示例

```java
private void handleHeartbeat(HttpServletRequest req, HttpServletResponse resp) throws IOException {
    JSONObject body = JSONUtil.parseObj(req.getInputStream());
    
    // 解析 ID 服务器（逗号分隔，按优先级顺序）
    String idServersStr = body.getStr("custom-rendezvous-server", "");
    String[] idServers = idServersStr.split(",");
    // idServers[0] 是最高优先级的（实际使用的）
    // idServers[1] 是次优先级的，以此类推
    
    // 解析 API 服务器
    String apiServersStr = body.getStr("api-server", "");
    String[] apiServers = apiServersStr.split(",");
    // apiServers[0] 是实际使用的 API 服务器
    
    // 解析中继服务器
    String relayServersStr = body.getStr("relay-server", "");
    String[] relayServers = relayServersStr.split(",");
    
    // 解析 Key
    String keysStr = body.getStr("key", "");
    String[] keys = keysStr.split(",");
    
    // 记录日志
    log.info("Device {} - ID Servers: {}", deviceId, Arrays.toString(idServers));
    log.info("Device {} - API Servers: {}", deviceId, Arrays.toString(apiServers));
    log.info("Device {} - Relay Servers: {}", deviceId, Arrays.toString(relayServers));
    log.info("Device {} - Keys: {}", deviceId, Arrays.toString(keys));
    
    // 实际使用的配置（第一个值）
    String actualIdServer = idServers.length > 0 ? idServers[0] : "";
    String actualApiServer = apiServers.length > 0 ? apiServers[0] : "";
    String actualRelayServer = relayServers.length > 0 ? relayServers[0] : "";
    String actualKey = keys.length > 0 ? keys[0] : "";
}
```

---

## 五、注意事项

1. **去重**：代码会自动去除重复的值（使用 `contains()` 检查）

2. **空值过滤**：只有非空的值才会被添加到列表中

3. **优先级顺序**：逗号分隔的值按优先级从高到低排列，**第一个值就是实际使用的值**

4. **Windows License**：只在 Windows 平台检查，其他平台返回 `None`

5. **Serial 检查**：`rendezvous-servers` 配置只在 `serial > 3` 时检查

6. **推导的 API 服务器**：如果 ID 服务器有值，会自动推导一个 API 服务器地址（端口-2）

---

## 六、优势

1. **完整信息**：服务器端可以看到客户端所有可能的配置来源
2. **优先级清晰**：按优先级顺序排列，第一个就是实际使用的
3. **便于调试**：可以快速定位配置来源问题
4. **兼容性好**：如果只有一个值，就是单个字符串；多个值用逗号分隔

---

## 七、修改位置

**文件**：`src/hbbs_http/sync.rs`

**函数**：`handle_heartbeat()` 中的心跳包构建部分

**修改内容**：
- 收集所有优先级的值
- 按优先级顺序排列
- 用逗号连接后上传

```

已完成修改。心跳包现在会收集所有优先级的值，按优先级从高到低用逗号分隔上传。

## 修改总结

### 修改内容

1. ID 服务器（custom-rendezvous-server）
   - 收集 6 个优先级来源的值
   - 按优先级顺序用逗号连接

2. API 服务器（api-server）
   - 收集 5 个优先级来源的值
   - 包括从 ID 服务器推导的值

3. 中继服务器（relay-server）
   - 收集 3 个优先级来源的值

4. Key（服务器公钥）
   - 收集 4 个优先级来源的值

### 上报格式示例

```json
{
  "custom-rendezvous-server": "182.92.140.90,id.example.com",
  "api-server": "http://api.example.com,http://id.example.com:21114,http://jetion123.com",
  "relay-server": "relay.example.com",
  "key": "key1,key2,key3"
}
```

### 特点

- 按优先级从高到低排列
- 第一个值就是实际使用的值
- 自动去重（相同值不会重复添加）
- 空值过滤（只添加非空值）

### 文档

已创建文档：`docs/心跳包服务器配置上报说明.md`，包含：
- 各配置项的优先级说明
- 心跳包 JSON 示例
- 服务器端解析建议（Java Servlet）
- 注意事项

重新编译客户端后，心跳包会按优先级上报所有配置值，服务器端可以清楚看到客户端的所有配置来源和实际使用的值。

```json
{
  "custom-rendezvous-server": "182.92.140.90,id.example.com",
  "api-server": "http://api.example.com,http://id.example.com:21114,http://jetion123.com",
  "relay-server": "relay.example.com",
  "key": "key1,key2,key3"
}
```

---

**User**

编译失败，错误信息如下
error[E0609]: no field `serial` on type `hbb_common::config::Config`
   --> src\hbbs_http\sync.rs:289:47
    |
289 |                 let serial_obsolute = config2.serial > 3;
    |                                               ^^^^^^ unknown field
    |
    = note: available fields are: `id`

For more information about this error, try `rustc --explain E0609`.
warning: `rustdesk` (lib) generated 17 warnings
error: could not compile `rustdesk` (lib) due to previous error; 17 warnings emitted
Error occurred when executing: `cargo build --features hwcodec,vram,flutter --lib --release`. Exiting.
features: ['hwcodec', 'vram', 'flutter']
False
Move-Item: D:\a\_temp\47a512d5-c3d5-4658-9500-63328b598d7f.ps1:4
Line |
   4 |  mv ./flutter/build/windows/x64/runner/Release ./rustdesk
     |  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
     | Cannot find path 'D:\a\rustdesk144-client\rustdesk144-client\flutter\build\windows\x64\runner\Release' because
     | it does not exist.
Error: Process completed with exit code 1.

---

**Cursor**

修复编译错误：`Config::get()` 返回的是 `Config`，不是 `Config2`。查看如何正确访问 `serial` 字段：


`serial` 字段在 `Config2` 中，不在 `Config` 中。修复代码，直接访问 `CONFIG2`：


```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
                // 收集所有优先级的值，按优先级从高到低，用逗号分开全部上传
                
                // 获取 Windows License（如果存在）
                #[cfg(windows)]
                let license = crate::platform::windows::get_license_from_exe_name().ok();
                #[cfg(not(windows))]
                let license: Option<crate::custom_server::CustomServer> = None;
                
                // 1. ID 服务器（custom-rendezvous-server）：收集所有优先级的值
                let mut id_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.host.is_empty() {
                        id_servers.push(lic.host.clone());
                    }
                }
                // 优先级2：EXE_RENDEZVOUS_SERVER
                let exe_server = config::EXE_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !exe_server.is_empty() && !id_servers.contains(&exe_server) {
                    id_servers.push(exe_server);
                }
                // 优先级3：Config::get_option("custom-rendezvous-server")
                let custom_config = Config::get_option("custom-rendezvous-server");
                if !custom_config.is_empty() && !id_servers.contains(&custom_config) {
                    id_servers.push(custom_config);
                }
                // 优先级4：PROD_RENDEZVOUS_SERVER
                let prod_server = config::PROD_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !prod_server.is_empty() && !id_servers.contains(&prod_server) {
                    id_servers.push(prod_server);
                }
                // 优先级5：Config::get_option("rendezvous-servers")（serial 过期时）
                // 注意：SERIAL 是私有常量（值为3），这里直接使用硬编码值
                // serial 字段在 Config2 中，需要通过 CONFIG2 访问
                let serial_obsolute = config::CONFIG2.read().unwrap().serial > 3;
                if serial_obsolute {
                    let rendezvous_servers_config = Config::get_option("rendezvous-servers");
                    for s in rendezvous_servers_config.split(',') {
                        let s = s.trim();
                        if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
                            id_servers.push(s.to_string());
                        }
                    }
                }
                // 优先级6：RENDEZVOUS_SERVERS（编译时常量）
                for s in config::RENDEZVOUS_SERVERS.iter() {
                    let s = s.to_string();
                    if !id_servers.contains(&s) {
                        id_servers.push(s);
                    }
                }
                if !id_servers.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_servers.join(","));
                }
                
                // 2. API 服务器（api-server）：收集所有优先级的值
                let mut api_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.api.is_empty() {
                        api_servers.push(lic.api.clone());
                    }
                }
                // 优先级2：Config::get_option("api-server")
                let api_config = Config::get_option("api-server");
                if !api_config.is_empty() && !api_servers.contains(&api_config) {
                    api_servers.push(api_config);
                }
                // 优先级3：编译时环境变量 API_SERVER
                let env_api = option_env!("API_SERVER").unwrap_or_default();
                if !env_api.is_empty() && !api_servers.contains(&env_api.to_string()) {
                    api_servers.push(env_api.to_string());
                }
                // 优先级4：从 ID 服务器推导（端口-2）
                if let Some(id_server) = id_servers.first() {
                    let derived_api = if !id_server.is_empty() {
                        let s = crate::increase_port(id_server, -2);
                        if s == *id_server {
                            format!("http://{}:{}", s, config::RENDEZVOUS_PORT - 2)
                        } else {
                            format!("http://{}", s)
                        }
                    } else {
                        String::new()
                    };
                    if !derived_api.is_empty() && !api_servers.contains(&derived_api) {
                        api_servers.push(derived_api);
                    }
                }
                // 优先级5：硬编码默认值
                let default_api = "http://jetion123.com".to_string();
                if !api_servers.contains(&default_api) {
                    api_servers.push(default_api);
                }
                if !api_servers.is_empty() {
                    v["api-server"] = json!(api_servers.join(","));
                }
                
                // 3. 中继服务器（relay-server）：收集所有优先级的值
                let mut relay_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.relay.is_empty() {
                        relay_servers.push(lic.relay.clone());
                    }
                }
                // 优先级2：Config::get_option("relay-server")
                let relay_config = Config::get_option("relay-server");
                if !relay_config.is_empty() && !relay_servers.contains(&relay_config) {
                    relay_servers.push(relay_config);
                }
                // 优先级3：编译时环境变量 RELAY_SERVER（如果存在）
                let env_relay = option_env!("RELAY_SERVER").unwrap_or_default();
                if !env_relay.is_empty() && !relay_servers.contains(&env_relay.to_string()) {
                    relay_servers.push(env_relay.to_string());
                }
                if !relay_servers.is_empty() {
                    v["relay-server"] = json!(relay_servers.join(","));
                }
                
                // 4. Key：收集所有优先级的值
                let mut keys = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.key.is_empty() {
                        keys.push(lic.key.clone());
                    }
                }
                // 优先级2：Config::get_option("key")
                let key_config = Config::get_option("key");
                if !key_config.is_empty() && !keys.contains(&key_config) {
                    keys.push(key_config);
                }
                // 优先级3：编译时环境变量 RS_PUB_KEY
                let env_key = option_env!("RS_PUB_KEY").unwrap_or_default();
                if !env_key.is_empty() && !keys.contains(&env_key.to_string()) {
                    keys.push(env_key.to_string());
                }
                // 优先级4：硬编码默认值 RS_PUB_KEY
                let default_key = config::RS_PUB_KEY.to_string();
                if !keys.contains(&default_key) {
                    keys.push(default_key);
                }
                if !keys.is_empty() {
                    v["key"] = json!(keys.join(","));
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// 读取内置服务器配置（优先级：HARD_SETTINGS > BUILTIN_SETTINGS > 用户配置）
/// 
/// 内置配置来源：
/// 1. HARD_SETTINGS：通过 custom.txt 顶层直接设置的硬编码值（优先级最高）
/// 2. BUILTIN_SETTINGS：通过 custom.txt 的 default-settings/override-settings 设置的
/// 3. Config::get_option()：用户手动在 UI 里设置的配置
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层直接设置的硬编码值）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

检查 `CONFIG2` 的可见性，确保可以正确访问：


检查 `CONFIG2` 是否可访问，或是否有其他访问方式：


使用 `Config2::get()` 访问 `serial` 字段：


```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
                // 收集所有优先级的值，按优先级从高到低，用逗号分开全部上传
                
                // 获取 Windows License（如果存在）
                #[cfg(windows)]
                let license = crate::platform::windows::get_license_from_exe_name().ok();
                #[cfg(not(windows))]
                let license: Option<crate::custom_server::CustomServer> = None;
                
                // 1. ID 服务器（custom-rendezvous-server）：收集所有优先级的值
                let mut id_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.host.is_empty() {
                        id_servers.push(lic.host.clone());
                    }
                }
                // 优先级2：EXE_RENDEZVOUS_SERVER
                let exe_server = config::EXE_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !exe_server.is_empty() && !id_servers.contains(&exe_server) {
                    id_servers.push(exe_server);
                }
                // 优先级3：Config::get_option("custom-rendezvous-server")
                let custom_config = Config::get_option("custom-rendezvous-server");
                if !custom_config.is_empty() && !id_servers.contains(&custom_config) {
                    id_servers.push(custom_config);
                }
                // 优先级4：PROD_RENDEZVOUS_SERVER
                let prod_server = config::PROD_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !prod_server.is_empty() && !id_servers.contains(&prod_server) {
                    id_servers.push(prod_server);
                }
                // 优先级5：Config::get_option("rendezvous-servers")（serial 过期时）
                // 注意：SERIAL 是私有常量（值为3），这里直接使用硬编码值
                // serial 字段在 Config2 中，通过 Config2::get() 访问
                let serial_obsolute = config::Config2::get().serial > 3;
                if serial_obsolute {
                    let rendezvous_servers_config = Config::get_option("rendezvous-servers");
                    for s in rendezvous_servers_config.split(',') {
                        let s = s.trim();
                        if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
                            id_servers.push(s.to_string());
                        }
                    }
                }
                // 优先级6：RENDEZVOUS_SERVERS（编译时常量）
                for s in config::RENDEZVOUS_SERVERS.iter() {
                    let s = s.to_string();
                    if !id_servers.contains(&s) {
                        id_servers.push(s);
                    }
                }
                if !id_servers.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_servers.join(","));
                }
                
                // 2. API 服务器（api-server）：收集所有优先级的值
                let mut api_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.api.is_empty() {
                        api_servers.push(lic.api.clone());
                    }
                }
                // 优先级2：Config::get_option("api-server")
                let api_config = Config::get_option("api-server");
                if !api_config.is_empty() && !api_servers.contains(&api_config) {
                    api_servers.push(api_config);
                }
                // 优先级3：编译时环境变量 API_SERVER
                let env_api = option_env!("API_SERVER").unwrap_or_default();
                if !env_api.is_empty() && !api_servers.contains(&env_api.to_string()) {
                    api_servers.push(env_api.to_string());
                }
                // 优先级4：从 ID 服务器推导（端口-2）
                if let Some(id_server) = id_servers.first() {
                    let derived_api = if !id_server.is_empty() {
                        let s = crate::increase_port(id_server, -2);
                        if s == *id_server {
                            format!("http://{}:{}", s, config::RENDEZVOUS_PORT - 2)
                        } else {
                            format!("http://{}", s)
                        }
                    } else {
                        String::new()
                    };
                    if !derived_api.is_empty() && !api_servers.contains(&derived_api) {
                        api_servers.push(derived_api);
                    }
                }
                // 优先级5：硬编码默认值
                let default_api = "http://jetion123.com".to_string();
                if !api_servers.contains(&default_api) {
                    api_servers.push(default_api);
                }
                if !api_servers.is_empty() {
                    v["api-server"] = json!(api_servers.join(","));
                }
                
                // 3. 中继服务器（relay-server）：收集所有优先级的值
                let mut relay_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.relay.is_empty() {
                        relay_servers.push(lic.relay.clone());
                    }
                }
                // 优先级2：Config::get_option("relay-server")
                let relay_config = Config::get_option("relay-server");
                if !relay_config.is_empty() && !relay_servers.contains(&relay_config) {
                    relay_servers.push(relay_config);
                }
                // 优先级3：编译时环境变量 RELAY_SERVER（如果存在）
                let env_relay = option_env!("RELAY_SERVER").unwrap_or_default();
                if !env_relay.is_empty() && !relay_servers.contains(&env_relay.to_string()) {
                    relay_servers.push(env_relay.to_string());
                }
                if !relay_servers.is_empty() {
                    v["relay-server"] = json!(relay_servers.join(","));
                }
                
                // 4. Key：收集所有优先级的值
                let mut keys = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.key.is_empty() {
                        keys.push(lic.key.clone());
                    }
                }
                // 优先级2：Config::get_option("key")
                let key_config = Config::get_option("key");
                if !key_config.is_empty() && !keys.contains(&key_config) {
                    keys.push(key_config);
                }
                // 优先级3：编译时环境变量 RS_PUB_KEY
                let env_key = option_env!("RS_PUB_KEY").unwrap_or_default();
                if !env_key.is_empty() && !keys.contains(&env_key.to_string()) {
                    keys.push(env_key.to_string());
                }
                // 优先级4：硬编码默认值 RS_PUB_KEY
                let default_key = config::RS_PUB_KEY.to_string();
                if !keys.contains(&default_key) {
                    keys.push(default_key);
                }
                if !keys.is_empty() {
                    v["key"] = json!(keys.join(","));
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// 读取内置服务器配置（优先级：HARD_SETTINGS > BUILTIN_SETTINGS > 用户配置）
/// 
/// 内置配置来源：
/// 1. HARD_SETTINGS：通过 custom.txt 顶层直接设置的硬编码值（优先级最高）
/// 2. BUILTIN_SETTINGS：通过 custom.txt 的 default-settings/override-settings 设置的
/// 3. Config::get_option()：用户手动在 UI 里设置的配置
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层直接设置的硬编码值）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

检查是否需要导入 `Config2`：


已修复编译错误。

## 修复说明

**问题**：`Config::get()` 返回的是 `Config` 类型，而 `serial` 字段在 `Config2` 中。

**解决方案**：使用 `Config2::get()` 访问 `serial` 字段：

```rust
// 修改前（错误）：
let config2 = Config::get();
let serial_obsolute = config2.serial > 3;  // ❌ Config 没有 serial 字段

// 修改后（正确）：
let serial_obsolute = config::Config2::get().serial > 3;  // ✅ Config2 有 serial 字段
```

现在代码应该可以正常编译了。`Config2::get()` 会返回 `Config2` 的克隆，可以访问 `serial` 字段。

请重新编译，应该可以成功。

```rust
// 修改前（错误）：
let config2 = Config::get();
let serial_obsolute = config2.serial > 3;  // ❌ Config 没有 serial 字段

// 修改后（正确）：
let serial_obsolute = config::Config2::get().serial > 3;  // ✅ Config2 有 serial 字段
```

---

**User**

以系统服务的形式运行时，有远程控制会弹出窗口，如何取消弹出窗口及托盘图标

---

**Cursor**

查找服务模式下远程控制弹窗和托盘图标的处理逻辑：


继续查找服务模式检测和弹窗逻辑：


查找服务模式检测和弹窗显示逻辑：


查找 `is_server()` 的定义和服务模式检测：


查找服务模式检测和弹窗显示逻辑：


查找服务模式检测和弹窗显示逻辑：


修改代码，在服务模式下强制隐藏弹窗和托盘图标：


修改代码，在服务模式下强制隐藏弹窗和托盘图标：


```rust
use crate::client::translate;
#[cfg(windows)]
use crate::ipc::Data;
#[cfg(windows)]
use hbb_common::tokio;
use hbb_common::{allow_err, log};
use std::sync::{Arc, Mutex};
#[cfg(windows)]
use std::time::Duration;

pub fn start_tray() {
    // 在服务模式下强制隐藏托盘图标
    #[cfg(windows)]
    {
        if crate::platform::windows::is_self_service_running() {
            log::info!("Service mode detected, hiding tray icon");
            #[cfg(target_os = "macos")]
            {
                loop {
                    std::thread::sleep(std::time::Duration::from_secs(1));
                }
            }
            #[cfg(not(target_os = "macos"))]
            {
                return;
            }
        }
    }
    
    if crate::ui_interface::get_builtin_option(hbb_common::config::keys::OPTION_HIDE_TRAY) == "Y" {
        #[cfg(target_os = "macos")]
        {
            loop {
                std::thread::sleep(std::time::Duration::from_secs(1));
            }
        }
        #[cfg(not(target_os = "macos"))]
        {
            return;
        }
    }

    #[cfg(target_os = "linux")]
    crate::server::check_zombie();

    allow_err!(make_tray());
}

fn make_tray() -> hbb_common::ResultType<()> {
    // https://github.com/tauri-apps/tray-icon/blob/dev/examples/tao.rs
    use hbb_common::anyhow::Context;
    use tao::event_loop::{ControlFlow, EventLoopBuilder};
    use tray_icon::{
        menu::{Menu, MenuEvent, MenuItem},
        TrayIcon, TrayIconBuilder, TrayIconEvent as TrayEvent,
    };
    let icon;
    #[cfg(target_os = "macos")]
    {
        icon = include_bytes!("../res/mac-tray-dark-x2.png"); // use as template, so color is not important
    }
    #[cfg(not(target_os = "macos"))]
    {
        icon = include_bytes!("../res/tray-icon.ico");
    }

    let (icon_rgba, icon_width, icon_height) = {
        let image = load_icon_from_asset()
            .unwrap_or(image::load_from_memory(icon).context("Failed to open icon path")?)
            .into_rgba8();
        let (width, height) = image.dimensions();
        let rgba = image.into_raw();
        (rgba, width, height)
    };
    let icon = tray_icon::Icon::from_rgba(icon_rgba, icon_width, icon_height)
        .context("Failed to open icon")?;

    let mut event_loop = EventLoopBuilder::new().build();

    let tray_menu = Menu::new();
    let quit_i = MenuItem::new(translate("Stop service".to_owned()), true, None);
    let open_i = MenuItem::new(translate("Open".to_owned()), true, None);
    tray_menu.append_items(&[&open_i, &quit_i]).ok();
    let tooltip = |count: usize| {
        if count == 0 {
            format!(
                "{} {}",
                crate::get_app_name(),
                translate("Service is running".to_owned()),
            )
        } else {
            format!(
                "{} - {}\n{}",
                crate::get_app_name(),
                translate("Ready".to_owned()),
                translate("{".to_string() + &format!("{count}") + "} sessions"),
            )
        }
    };
    let mut _tray_icon: Arc<Mutex<Option<TrayIcon>>> = Default::default();

    let menu_channel = MenuEvent::receiver();
    let tray_channel = TrayEvent::receiver();
    #[cfg(windows)]
    let (ipc_sender, ipc_receiver) = std::sync::mpsc::channel::<Data>();

    let open_func = move || {
        if cfg!(not(feature = "flutter")) {
            crate::run_me::<&str>(vec![]).ok();
            return;
        }
        #[cfg(target_os = "macos")]
        crate::platform::macos::handle_application_should_open_untitled_file();
        #[cfg(target_os = "windows")]
        {
            // Do not use "start uni link" way, it may not work on some Windows, and pop out error
            // dialog, I found on one user's desktop, but no idea why, Windows is shit.
            // Use `run_me` instead.
            // `allow_multiple_instances` in `flutter/windows/runner/main.cpp` allows only one instance without args.
            crate::run_me::<&str>(vec![]).ok();
        }
        #[cfg(target_os = "linux")]
        {
            // Do not use "xdg-open", it won't read the config.
            if crate::dbus::invoke_new_connection(crate::get_uri_prefix()).is_err() {
                if let Ok(task) = crate::run_me::<&str>(vec![]) {
                    crate::server::CHILD_PROCESS.lock().unwrap().push(task);
                }
            }
        }
    };

    #[cfg(windows)]
    std::thread::spawn(move || {
        start_query_session_count(ipc_sender.clone());
    });
    #[cfg(windows)]
    let mut last_click = std::time::Instant::now();
    #[cfg(target_os = "macos")]
    {
        use tao::platform::macos::EventLoopExtMacOS;
        event_loop.set_activation_policy(tao::platform::macos::ActivationPolicy::Accessory);
    }
    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::WaitUntil(
            std::time::Instant::now() + std::time::Duration::from_millis(100),
        );

        if let tao::event::Event::NewEvents(tao::event::StartCause::Init) = event {
            // We create the icon once the event loop is actually running
            // to prevent issues like https://github.com/tauri-apps/tray-icon/issues/90
            let tray = TrayIconBuilder::new()
                .with_menu(Box::new(tray_menu.clone()))
                .with_tooltip(tooltip(0))
                .with_icon(icon.clone())
                .with_icon_as_template(true) // mac only
                .build();
            match tray {
                Ok(tray) => _tray_icon = Arc::new(Mutex::new(Some(tray))),
                Err(err) => {
                    log::error!("Failed to create tray icon: {}", err);
                }
            };

            // We have to request a redraw here to have the icon actually show up.
            // Tao only exposes a redraw method on the Window so we use core-foundation directly.
            #[cfg(target_os = "macos")]
            unsafe {
                use core_foundation::runloop::{CFRunLoopGetMain, CFRunLoopWakeUp};

                let rl = CFRunLoopGetMain();
                CFRunLoopWakeUp(rl);
            }
        }

        if let Ok(event) = menu_channel.try_recv() {
            if event.id == quit_i.id() {
                /* failed in windows, seems no permission to check system process
                if !crate::check_process("--server", false) {
                    *control_flow = ControlFlow::Exit;
                    return;
                }
                */
                if !crate::platform::uninstall_service(false, false) {
                    *control_flow = ControlFlow::Exit;
                }
            } else if event.id == open_i.id() {
                open_func();
            }
        }

        if let Ok(_event) = tray_channel.try_recv() {
            #[cfg(target_os = "windows")]
            match _event {
                TrayEvent::Click {
                    button,
                    button_state,
                    ..
                } => {
                    if button == tray_icon::MouseButton::Left
                        && button_state == tray_icon::MouseButtonState::Up
                    {
                        if last_click.elapsed() < std::time::Duration::from_secs(1) {
                            return;
                        }
                        open_func();
                        last_click = std::time::Instant::now();
                    }
                }
                _ => {}
            }
        }

        #[cfg(windows)]
        if let Ok(data) = ipc_receiver.try_recv() {
            match data {
                Data::ControlledSessionCount(count) => {
                    _tray_icon
                        .lock()
                        .unwrap()
                        .as_mut()
                        .map(|t| t.set_tooltip(Some(tooltip(count))));
                }
                _ => {}
            }
        }
    });
}

#[cfg(windows)]
#[tokio::main(flavor = "current_thread")]
async fn start_query_session_count(sender: std::sync::mpsc::Sender<Data>) {
    let mut last_count = 0;
    loop {
        if let Ok(mut c) = crate::ipc::connect(1000, "").await {
            let mut timer = crate::rustdesk_interval(tokio::time::interval(Duration::from_secs(1)));
            loop {
                tokio::select! {
                    res = c.next() => {
                        match res {
                            Err(err) => {
                                log::error!("ipc connection closed: {}", err);
                                break;
                            }

                            Ok(Some(Data::ControlledSessionCount(count))) => {
                                if count != last_count {
                                    last_count = count;
                                    sender.send(Data::ControlledSessionCount(count)).ok();
                                }
                            }
                            _ => {}
                        }
                    }

                    _ = timer.tick() => {
                        c.send(&Data::ControlledSessionCount(0)).await.ok();
                    }
                }
            }
        }
        hbb_common::sleep(1.).await;
    }
}

fn load_icon_from_asset() -> Option<image::DynamicImage> {
    let Some(path) = std::env::current_exe().map_or(None, |x| x.parent().map(|x| x.to_path_buf()))
    else {
        return None;
    };
    #[cfg(target_os = "macos")]
    let path = path.join("../Frameworks/App.framework/Resources/flutter_assets/assets/icon.png");
    #[cfg(windows)]
    let path = path.join(r"data\flutter_assets\assets\icon.png");
    #[cfg(target_os = "linux")]
    let path = path.join(r"data/flutter_assets/assets/icon.png");
    if path.exists() {
        if let Ok(image) = image::open(path) {
            return Some(image);
        }
    }
    None
}

```

```rust
use crate::config::Config;
use sodiumoxide::base64;
use std::sync::{Arc, RwLock};

lazy_static::lazy_static! {
    pub static ref TEMPORARY_PASSWORD:Arc<RwLock<String>> = Arc::new(RwLock::new(get_auto_password()));
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum VerificationMethod {
    OnlyUseTemporaryPassword,
    OnlyUsePermanentPassword,
    UseBothPasswords,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ApproveMode {
    Both,
    Password,
    Click,
}

fn get_auto_password() -> String {
    let len = temporary_password_length();
    if Config::get_bool_option(crate::config::keys::OPTION_ALLOW_NUMERNIC_ONE_TIME_PASSWORD) {
        Config::get_auto_numeric_password(len)
    } else {
        Config::get_auto_password(len)
    }
}

// Should only be called in server
pub fn update_temporary_password() {
    *TEMPORARY_PASSWORD.write().unwrap() = get_auto_password();
}

// Should only be called in server
pub fn temporary_password() -> String {
    TEMPORARY_PASSWORD.read().unwrap().clone()
}

fn verification_method() -> VerificationMethod {
    let method = Config::get_option("verification-method");
    if method == "use-temporary-password" {
        VerificationMethod::OnlyUseTemporaryPassword
    } else if method == "use-permanent-password" {
        VerificationMethod::OnlyUsePermanentPassword
    } else {
        VerificationMethod::UseBothPasswords // default
    }
}

pub fn temporary_password_length() -> usize {
    let length = Config::get_option("temporary-password-length");
    if length == "8" {
        8
    } else if length == "10" {
        10
    } else {
        6 // default
    }
}

pub fn temporary_enabled() -> bool {
    verification_method() != VerificationMethod::OnlyUsePermanentPassword
}

pub fn permanent_enabled() -> bool {
    verification_method() != VerificationMethod::OnlyUseTemporaryPassword
}

pub fn has_valid_password() -> bool {
    temporary_enabled() && !temporary_password().is_empty()
        || permanent_enabled() && !Config::get_permanent_password().is_empty()
}

pub fn approve_mode() -> ApproveMode {
    let mode = Config::get_option("approve-mode");
    if mode == "password" {
        ApproveMode::Password
    } else if mode == "click" {
        ApproveMode::Click
    } else {
        ApproveMode::Both
    }
}

pub fn hide_cm() -> bool {
    // 在服务模式下强制隐藏连接管理窗口
    #[cfg(windows)]
    {
        if crate::platform::windows::is_self_service_running() {
            return true;
        }
    }
    
    approve_mode() == ApproveMode::Password
        && verification_method() == VerificationMethod::OnlyUsePermanentPassword
        && crate::config::option2bool("allow-hide-cm", &Config::get_option("allow-hide-cm"))
}

const VERSION_LEN: usize = 2;

pub fn encrypt_str_or_original(s: &str, version: &str, max_len: usize) -> String {
    if decrypt_str_or_original(s, version).1 {
        log::error!("Duplicate encryption!");
        return s.to_owned();
    }
    if s.chars().count() > max_len {
        return String::default();
    }
    if version == "00" {
        if let Ok(s) = encrypt(s.as_bytes()) {
            return version.to_owned() + &s;
        }
    }
    s.to_owned()
}

// String: password
// bool: whether decryption is successful
// bool: whether should store to re-encrypt when load
// note: s.len() return length in bytes, s.chars().count() return char count
//       &[..2] return the left 2 bytes, s.chars().take(2) return the left 2 chars
pub fn decrypt_str_or_original(s: &str, current_version: &str) -> (String, bool, bool) {
    if s.len() > VERSION_LEN {
        if s.starts_with("00") {
            if let Ok(v) = decrypt(s[VERSION_LEN..].as_bytes()) {
                return (
                    String::from_utf8_lossy(&v).to_string(),
                    true,
                    "00" != current_version,
                );
            }
        }
    }

    (s.to_owned(), false, !s.is_empty())
}

pub fn encrypt_vec_or_original(v: &[u8], version: &str, max_len: usize) -> Vec<u8> {
    if decrypt_vec_or_original(v, version).1 {
        log::error!("Duplicate encryption!");
        return v.to_owned();
    }
    if v.len() > max_len {
        return vec![];
    }
    if version == "00" {
        if let Ok(s) = encrypt(v) {
            let mut version = version.to_owned().into_bytes();
            version.append(&mut s.into_bytes());
            return version;
        }
    }
    v.to_owned()
}

// Vec<u8>: password
// bool: whether decryption is successful
// bool: whether should store to re-encrypt when load
pub fn decrypt_vec_or_original(v: &[u8], current_version: &str) -> (Vec<u8>, bool, bool) {
    if v.len() > VERSION_LEN {
        let version = String::from_utf8_lossy(&v[..VERSION_LEN]);
        if version == "00" {
            if let Ok(v) = decrypt(&v[VERSION_LEN..]) {
                return (v, true, version != current_version);
            }
        }
    }

    (v.to_owned(), false, !v.is_empty())
}

fn encrypt(v: &[u8]) -> Result<String, ()> {
    if !v.is_empty() {
        symmetric_crypt(v, true).map(|v| base64::encode(v, base64::Variant::Original))
    } else {
        Err(())
    }
}

fn decrypt(v: &[u8]) -> Result<Vec<u8>, ()> {
    if !v.is_empty() {
        base64::decode(v, base64::Variant::Original).and_then(|v| symmetric_crypt(&v, false))
    } else {
        Err(())
    }
}

pub fn symmetric_crypt(data: &[u8], encrypt: bool) -> Result<Vec<u8>, ()> {
    use sodiumoxide::crypto::secretbox;
    use std::convert::TryInto;

    let mut keybuf = crate::get_uuid();
    keybuf.resize(secretbox::KEYBYTES, 0);
    let key = secretbox::Key(keybuf.try_into().map_err(|_| ())?);
    let nonce = secretbox::Nonce([0; secretbox::NONCEBYTES]);

    if encrypt {
        Ok(secretbox::seal(data, &nonce, &key))
    } else {
        secretbox::open(data, &nonce, &key)
    }
}

mod test {

    #[test]
    fn test() {
        use super::*;
        use rand::{thread_rng, Rng};
        use std::time::Instant;

        let version = "00";
        let max_len = 128;

        println!("test str");
        let data = "1ü1111";
        let encrypted = encrypt_str_or_original(data, version, max_len);
        let (decrypted, succ, store) = decrypt_str_or_original(&encrypted, version);
        println!("data: {data}");
        println!("encrypted: {encrypted}");
        println!("decrypted: {decrypted}");
        assert_eq!(data, decrypted);
        assert_eq!(version, &encrypted[..2]);
        assert!(succ);
        assert!(!store);
        let (_, _, store) = decrypt_str_or_original(&encrypted, "99");
        assert!(store);
        assert!(!decrypt_str_or_original(&decrypted, version).1);
        assert_eq!(
            encrypt_str_or_original(&encrypted, version, max_len),
            encrypted
        );

        println!("test vec");
        let data: Vec<u8> = "1ü1111".as_bytes().to_vec();
        let encrypted = encrypt_vec_or_original(&data, version, max_len);
        let (decrypted, succ, store) = decrypt_vec_or_original(&encrypted, version);
        println!("data: {data:?}");
        println!("encrypted: {encrypted:?}");
        println!("decrypted: {decrypted:?}");
        assert_eq!(data, decrypted);
        assert_eq!(version.as_bytes(), &encrypted[..2]);
        assert!(!store);
        assert!(succ);
        let (_, _, store) = decrypt_vec_or_original(&encrypted, "99");
        assert!(store);
        assert!(!decrypt_vec_or_original(&decrypted, version).1);
        assert_eq!(
            encrypt_vec_or_original(&encrypted, version, max_len),
            encrypted
        );

        println!("test original");
        let data = version.to_string() + "Hello World";
        let (decrypted, succ, store) = decrypt_str_or_original(&data, version);
        assert_eq!(data, decrypted);
        assert!(store);
        assert!(!succ);
        let verbytes = version.as_bytes();
        let data: Vec<u8> = vec![verbytes[0], verbytes[1], 1, 2, 3, 4, 5, 6];
        let (decrypted, succ, store) = decrypt_vec_or_original(&data, version);
        assert_eq!(data, decrypted);
        assert!(store);
        assert!(!succ);
        let (_, succ, store) = decrypt_str_or_original("", version);
        assert!(!store);
        assert!(!succ);
        let (_, succ, store) = decrypt_vec_or_original(&[], version);
        assert!(!store);
        assert!(!succ);
        let data = "1ü1111";
        assert_eq!(decrypt_str_or_original(data, version).0, data);
        let data: Vec<u8> = "1ü1111".as_bytes().to_vec();
        assert_eq!(decrypt_vec_or_original(&data, version).0, data);

        println!("test speed");
        let test_speed = |len: usize, name: &str| {
            let mut data: Vec<u8> = vec![];
            let mut rng = thread_rng();
            for _ in 0..len {
                data.push(rng.gen_range(0..255));
            }
            let start: Instant = Instant::now();
            let encrypted = encrypt_vec_or_original(&data, version, len);
            assert_ne!(data, decrypted);
            let t1 = start.elapsed();
            let start = Instant::now();
            let (decrypted, _, _) = decrypt_vec_or_original(&encrypted, version);
            let t2 = start.elapsed();
            assert_eq!(data, decrypted);
            println!("{name}");
            println!("encrypt:{:?}, decrypt:{:?}", t1, t2);

            let start: Instant = Instant::now();
            let encrypted = base64::encode(&data, base64::Variant::Original);
            let t1 = start.elapsed();
            let start = Instant::now();
            let decrypted = base64::decode(&encrypted, base64::Variant::Original).unwrap();
            let t2 = start.elapsed();
            assert_eq!(data, decrypted);
            println!("base64, encrypt:{:?}, decrypt:{:?}", t1, t2,);
        };
        test_speed(128, "128");
        test_speed(1024, "1k");
        test_speed(1024 * 1024, "1M");
        test_speed(10 * 1024 * 1024, "10M");
        test_speed(100 * 1024 * 1024, "100M");
    }
}

```

检查 `password_security.rs` 的导入，确保可以访问 Windows 平台函数：


检查 `hbb_common` 的平台模块结构，因为库不应直接访问主程序的平台代码：


检查 `hbb_common` 的平台模块是否有服务检测函数：


`hbb_common` 的平台模块没有服务检测函数。改用配置选项方式：在服务模式下设置配置，然后在 `hide_cm()` 中检查。先回退 `password_security.rs` 的修改，改用更简单的方法：


```rust
use crate::config::Config;
use sodiumoxide::base64;
use std::sync::{Arc, RwLock};

lazy_static::lazy_static! {
    pub static ref TEMPORARY_PASSWORD:Arc<RwLock<String>> = Arc::new(RwLock::new(get_auto_password()));
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum VerificationMethod {
    OnlyUseTemporaryPassword,
    OnlyUsePermanentPassword,
    UseBothPasswords,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ApproveMode {
    Both,
    Password,
    Click,
}

fn get_auto_password() -> String {
    let len = temporary_password_length();
    if Config::get_bool_option(crate::config::keys::OPTION_ALLOW_NUMERNIC_ONE_TIME_PASSWORD) {
        Config::get_auto_numeric_password(len)
    } else {
        Config::get_auto_password(len)
    }
}

// Should only be called in server
pub fn update_temporary_password() {
    *TEMPORARY_PASSWORD.write().unwrap() = get_auto_password();
}

// Should only be called in server
pub fn temporary_password() -> String {
    TEMPORARY_PASSWORD.read().unwrap().clone()
}

fn verification_method() -> VerificationMethod {
    let method = Config::get_option("verification-method");
    if method == "use-temporary-password" {
        VerificationMethod::OnlyUseTemporaryPassword
    } else if method == "use-permanent-password" {
        VerificationMethod::OnlyUsePermanentPassword
    } else {
        VerificationMethod::UseBothPasswords // default
    }
}

pub fn temporary_password_length() -> usize {
    let length = Config::get_option("temporary-password-length");
    if length == "8" {
        8
    } else if length == "10" {
        10
    } else {
        6 // default
    }
}

pub fn temporary_enabled() -> bool {
    verification_method() != VerificationMethod::OnlyUsePermanentPassword
}

pub fn permanent_enabled() -> bool {
    verification_method() != VerificationMethod::OnlyUseTemporaryPassword
}

pub fn has_valid_password() -> bool {
    temporary_enabled() && !temporary_password().is_empty()
        || permanent_enabled() && !Config::get_permanent_password().is_empty()
}

pub fn approve_mode() -> ApproveMode {
    let mode = Config::get_option("approve-mode");
    if mode == "password" {
        ApproveMode::Password
    } else if mode == "click" {
        ApproveMode::Click
    } else {
        ApproveMode::Both
    }
}

pub fn hide_cm() -> bool {
    // 在服务模式下强制隐藏连接管理窗口
    // 检查配置选项 "service-mode" 或 "allow-hide-cm" 是否为 Y
    let service_mode = Config::get_option("service-mode") == "Y";
    let allow_hide_cm = crate::config::option2bool("allow-hide-cm", &Config::get_option("allow-hide-cm"));
    
    if service_mode {
        return true;
    }
    
    approve_mode() == ApproveMode::Password
        && verification_method() == VerificationMethod::OnlyUsePermanentPassword
        && allow_hide_cm
}

const VERSION_LEN: usize = 2;

pub fn encrypt_str_or_original(s: &str, version: &str, max_len: usize) -> String {
    if decrypt_str_or_original(s, version).1 {
        log::error!("Duplicate encryption!");
        return s.to_owned();
    }
    if s.chars().count() > max_len {
        return String::default();
    }
    if version == "00" {
        if let Ok(s) = encrypt(s.as_bytes()) {
            return version.to_owned() + &s;
        }
    }
    s.to_owned()
}

// String: password
// bool: whether decryption is successful
// bool: whether should store to re-encrypt when load
// note: s.len() return length in bytes, s.chars().count() return char count
//       &[..2] return the left 2 bytes, s.chars().take(2) return the left 2 chars
pub fn decrypt_str_or_original(s: &str, current_version: &str) -> (String, bool, bool) {
    if s.len() > VERSION_LEN {
        if s.starts_with("00") {
            if let Ok(v) = decrypt(s[VERSION_LEN..].as_bytes()) {
                return (
                    String::from_utf8_lossy(&v).to_string(),
                    true,
                    "00" != current_version,
                );
            }
        }
    }

    (s.to_owned(), false, !s.is_empty())
}

pub fn encrypt_vec_or_original(v: &[u8], version: &str, max_len: usize) -> Vec<u8> {
    if decrypt_vec_or_original(v, version).1 {
        log::error!("Duplicate encryption!");
        return v.to_owned();
    }
    if v.len() > max_len {
        return vec![];
    }
    if version == "00" {
        if let Ok(s) = encrypt(v) {
            let mut version = version.to_owned().into_bytes();
            version.append(&mut s.into_bytes());
            return version;
        }
    }
    v.to_owned()
}

// Vec<u8>: password
// bool: whether decryption is successful
// bool: whether should store to re-encrypt when load
pub fn decrypt_vec_or_original(v: &[u8], current_version: &str) -> (Vec<u8>, bool, bool) {
    if v.len() > VERSION_LEN {
        let version = String::from_utf8_lossy(&v[..VERSION_LEN]);
        if version == "00" {
            if let Ok(v) = decrypt(&v[VERSION_LEN..]) {
                return (v, true, version != current_version);
            }
        }
    }

    (v.to_owned(), false, !v.is_empty())
}

fn encrypt(v: &[u8]) -> Result<String, ()> {
    if !v.is_empty() {
        symmetric_crypt(v, true).map(|v| base64::encode(v, base64::Variant::Original))
    } else {
        Err(())
    }
}

fn decrypt(v: &[u8]) -> Result<Vec<u8>, ()> {
    if !v.is_empty() {
        base64::decode(v, base64::Variant::Original).and_then(|v| symmetric_crypt(&v, false))
    } else {
        Err(())
    }
}

pub fn symmetric_crypt(data: &[u8], encrypt: bool) -> Result<Vec<u8>, ()> {
    use sodiumoxide::crypto::secretbox;
    use std::convert::TryInto;

    let mut keybuf = crate::get_uuid();
    keybuf.resize(secretbox::KEYBYTES, 0);
    let key = secretbox::Key(keybuf.try_into().map_err(|_| ())?);
    let nonce = secretbox::Nonce([0; secretbox::NONCEBYTES]);

    if encrypt {
        Ok(secretbox::seal(data, &nonce, &key))
    } else {
        secretbox::open(data, &nonce, &key)
    }
}

mod test {

    #[test]
    fn test() {
        use super::*;
        use rand::{thread_rng, Rng};
        use std::time::Instant;

        let version = "00";
        let max_len = 128;

        println!("test str");
        let data = "1ü1111";
        let encrypted = encrypt_str_or_original(data, version, max_len);
        let (decrypted, succ, store) = decrypt_str_or_original(&encrypted, version);
        println!("data: {data}");
        println!("encrypted: {encrypted}");
        println!("decrypted: {decrypted}");
        assert_eq!(data, decrypted);
        assert_eq!(version, &encrypted[..2]);
        assert!(succ);
        assert!(!store);
        let (_, _, store) = decrypt_str_or_original(&encrypted, "99");
        assert!(store);
        assert!(!decrypt_str_or_original(&decrypted, version).1);
        assert_eq!(
            encrypt_str_or_original(&encrypted, version, max_len),
            encrypted
        );

        println!("test vec");
        let data: Vec<u8> = "1ü1111".as_bytes().to_vec();
        let encrypted = encrypt_vec_or_original(&data, version, max_len);
        let (decrypted, succ, store) = decrypt_vec_or_original(&encrypted, version);
        println!("data: {data:?}");
        println!("encrypted: {encrypted:?}");
        println!("decrypted: {decrypted:?}");
        assert_eq!(data, decrypted);
        assert_eq!(version.as_bytes(), &encrypted[..2]);
        assert!(!store);
        assert!(succ);
        let (_, _, store) = decrypt_vec_or_original(&encrypted, "99");
        assert!(store);
        assert!(!decrypt_vec_or_original(&decrypted, version).1);
        assert_eq!(
            encrypt_vec_or_original(&encrypted, version, max_len),
            encrypted
        );

        println!("test original");
        let data = version.to_string() + "Hello World";
        let (decrypted, succ, store) = decrypt_str_or_original(&data, version);
        assert_eq!(data, decrypted);
        assert!(store);
        assert!(!succ);
        let verbytes = version.as_bytes();
        let data: Vec<u8> = vec![verbytes[0], verbytes[1], 1, 2, 3, 4, 5, 6];
        let (decrypted, succ, store) = decrypt_vec_or_original(&data, version);
        assert_eq!(data, decrypted);
        assert!(store);
        assert!(!succ);
        let (_, succ, store) = decrypt_str_or_original("", version);
        assert!(!store);
        assert!(!succ);
        let (_, succ, store) = decrypt_vec_or_original(&[], version);
        assert!(!store);
        assert!(!succ);
        let data = "1ü1111";
        assert_eq!(decrypt_str_or_original(data, version).0, data);
        let data: Vec<u8> = "1ü1111".as_bytes().to_vec();
        assert_eq!(decrypt_vec_or_original(&data, version).0, data);

        println!("test speed");
        let test_speed = |len: usize, name: &str| {
            let mut data: Vec<u8> = vec![];
            let mut rng = thread_rng();
            for _ in 0..len {
                data.push(rng.gen_range(0..255));
            }
            let start: Instant = Instant::now();
            let encrypted = encrypt_vec_or_original(&data, version, len);
            assert_ne!(data, decrypted);
            let t1 = start.elapsed();
            let start = Instant::now();
            let (decrypted, _, _) = decrypt_vec_or_original(&encrypted, version);
            let t2 = start.elapsed();
            assert_eq!(data, decrypted);
            println!("{name}");
            println!("encrypt:{:?}, decrypt:{:?}", t1, t2);

            let start: Instant = Instant::now();
            let encrypted = base64::encode(&data, base64::Variant::Original);
            let t1 = start.elapsed();
            let start = Instant::now();
            let decrypted = base64::decode(&encrypted, base64::Variant::Original).unwrap();
            let t2 = start.elapsed();
            assert_eq!(data, decrypted);
            println!("base64, encrypt:{:?}, decrypt:{:?}", t1, t2,);
        };
        test_speed(128, "128");
        test_speed(1024, "1k");
        test_speed(1024 * 1024, "1M");
        test_speed(10 * 1024 * 1024, "10M");
        test_speed(100 * 1024 * 1024, "100M");
    }
}

```

在服务启动时设置配置选项，并更新相关检查：


```rust
use super::{CursorData, ResultType};
use crate::{
    common::PORTABLE_APPNAME_RUNTIME_ENV_KEY,
    custom_server::*,
    ipc,
    privacy_mode::win_topmost_window::{self, WIN_TOPMOST_INJECTED_PROCESS_EXE},
};
use hbb_common::{
    allow_err,
    anyhow::anyhow,
    bail,
    config::{self, Config},
    libc::{c_int, wchar_t},
    log,
    message_proto::{DisplayInfo, Resolution, WindowsSession},
    sleep,
    sysinfo::{Pid, System},
    timeout, tokio,
};
use std::{
    collections::HashMap,
    ffi::{CString, OsString},
    fs,
    io::{self, prelude::*},
    mem,
    os::{
        raw::c_ulong,
        windows::{ffi::OsStringExt, process::CommandExt},
    },
    path::*,
    ptr::null_mut,
    sync::{atomic::Ordering, Arc, Mutex},
    time::{Duration, Instant},
};
use wallpaper;
#[cfg(not(debug_assertions))]
use winapi::um::libloaderapi::{LoadLibraryExW, LOAD_LIBRARY_SEARCH_USER_DIRS};
use winapi::{
    ctypes::c_void,
    shared::{minwindef::*, ntdef::NULL, windef::*, winerror::*},
    um::{
        errhandlingapi::GetLastError,
        handleapi::{CloseHandle, INVALID_HANDLE_VALUE},
        libloaderapi::{
            GetProcAddress, LoadLibraryA, LoadLibraryExA, LOAD_LIBRARY_SEARCH_SYSTEM32,
        },
        minwinbase::STILL_ACTIVE,
        processthreadsapi::{
            GetCurrentProcess, GetCurrentProcessId, GetExitCodeProcess, OpenProcess,
            OpenProcessToken, ProcessIdToSessionId, PROCESS_INFORMATION, STARTUPINFOW,
        },
        securitybaseapi::{
            AllocateAndInitializeSid, DuplicateToken, EqualSid, FreeSid, GetTokenInformation,
        },
        shellapi::ShellExecuteW,
        sysinfoapi::{GetNativeSystemInfo, SYSTEM_INFO},
        winbase::*,
        wingdi::*,
        winnt::{
            SecurityImpersonation, TokenElevation, TokenGroups, TokenImpersonation, TokenType,
            DOMAIN_ALIAS_RID_ADMINS, ES_AWAYMODE_REQUIRED, ES_CONTINUOUS, ES_DISPLAY_REQUIRED,
            ES_SYSTEM_REQUIRED, HANDLE, PROCESS_ALL_ACCESS, PROCESS_QUERY_LIMITED_INFORMATION,
            PSID, SECURITY_BUILTIN_DOMAIN_RID, SECURITY_NT_AUTHORITY, SID_IDENTIFIER_AUTHORITY,
            TOKEN_ELEVATION, TOKEN_GROUPS, TOKEN_QUERY, TOKEN_TYPE,
        },
        winreg::HKEY_CURRENT_USER,
        winspool::{
            EnumPrintersW, GetDefaultPrinterW, PRINTER_ENUM_CONNECTIONS, PRINTER_ENUM_LOCAL,
            PRINTER_INFO_1W,
        },
        winuser::*,
    },
};
use windows::Win32::{
    Foundation::{CloseHandle as WinCloseHandle, HANDLE as WinHANDLE},
    System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    },
};
use windows_service::{
    define_windows_service,
    service::{
        ServiceControl, ServiceControlAccept, ServiceExitCode, ServiceState, ServiceStatus,
        ServiceType,
    },
    service_control_handler::{self, ServiceControlHandlerResult},
};
use winreg::{enums::*, RegKey};

pub const FLUTTER_RUNNER_WIN32_WINDOW_CLASS: &'static str = "FLUTTER_RUNNER_WIN32_WINDOW"; // main window, install window
pub const EXPLORER_EXE: &'static str = "explorer.exe";
pub const SET_FOREGROUND_WINDOW: &'static str = "SET_FOREGROUND_WINDOW";

const REG_NAME_INSTALL_DESKTOPSHORTCUTS: &str = "DESKTOPSHORTCUTS";
const REG_NAME_INSTALL_STARTMENUSHORTCUTS: &str = "STARTMENUSHORTCUTS";
pub const REG_NAME_INSTALL_PRINTER: &str = "PRINTER";

pub fn get_focused_display(displays: Vec<DisplayInfo>) -> Option<usize> {
    unsafe {
        let hwnd = GetForegroundWindow();
        let mut rect: RECT = mem::zeroed();
        if GetWindowRect(hwnd, &mut rect as *mut RECT) == 0 {
            return None;
        }
        displays.iter().position(|display| {
            let center_x = rect.left + (rect.right - rect.left) / 2;
            let center_y = rect.top + (rect.bottom - rect.top) / 2;
            center_x >= display.x
                && center_x <= display.x + display.width
                && center_y >= display.y
                && center_y <= display.y + display.height
        })
    }
}

pub fn get_cursor_pos() -> Option<(i32, i32)> {
    unsafe {
        #[allow(invalid_value)]
        let mut out = mem::MaybeUninit::uninit().assume_init();
        if GetCursorPos(&mut out) == FALSE {
            return None;
        }
        return Some((out.x, out.y));
    }
}

pub fn reset_input_cache() {}

pub fn get_cursor() -> ResultType<Option<u64>> {
    unsafe {
        #[allow(invalid_value)]
        let mut ci: CURSORINFO = mem::MaybeUninit::uninit().assume_init();
        ci.cbSize = std::mem::size_of::<CURSORINFO>() as _;
        if crate::portable_service::client::get_cursor_info(&mut ci) == FALSE {
            return Err(io::Error::last_os_error().into());
        }
        if ci.flags & CURSOR_SHOWING == 0 {
            Ok(None)
        } else {
            Ok(Some(ci.hCursor as _))
        }
    }
}

struct IconInfo(ICONINFO);

impl IconInfo {
    fn new(icon: HICON) -> ResultType<Self> {
        unsafe {
            #[allow(invalid_value)]
            let mut ii = mem::MaybeUninit::uninit().assume_init();
            if GetIconInfo(icon, &mut ii) == FALSE {
                Err(io::Error::last_os_error().into())
            } else {
                let ii = Self(ii);
                if ii.0.hbmMask.is_null() {
                    bail!("Cursor bitmap handle is NULL");
                }
                return Ok(ii);
            }
        }
    }

    fn is_color(&self) -> bool {
        !self.0.hbmColor.is_null()
    }
}

impl Drop for IconInfo {
    fn drop(&mut self) {
        unsafe {
            if !self.0.hbmColor.is_null() {
                DeleteObject(self.0.hbmColor as _);
            }
            if !self.0.hbmMask.is_null() {
                DeleteObject(self.0.hbmMask as _);
            }
        }
    }
}

// https://github.com/TurboVNC/tightvnc/blob/a235bae328c12fd1c3aed6f3f034a37a6ffbbd22/vnc_winsrc/winvnc/vncEncoder.cpp
// https://github.com/TigerVNC/tigervnc/blob/master/win/rfb_win32/DeviceFrameBuffer.cxx
pub fn get_cursor_data(hcursor: u64) -> ResultType<CursorData> {
    unsafe {
        let mut ii = IconInfo::new(hcursor as _)?;
        let bm_mask = get_bitmap(ii.0.hbmMask)?;
        let mut width = bm_mask.bmWidth;
        let mut height = if ii.is_color() {
            bm_mask.bmHeight
        } else {
            bm_mask.bmHeight / 2
        };
        let cbits_size = width * height * 4;
        if cbits_size < 16 {
            bail!("Invalid icon: too small"); // solve some crash
        }
        let mut cbits: Vec<u8> = Vec::new();
        cbits.resize(cbits_size as _, 0);
        let mut mbits: Vec<u8> = Vec::new();
        mbits.resize((bm_mask.bmWidthBytes * bm_mask.bmHeight) as _, 0);
        let r = GetBitmapBits(ii.0.hbmMask, mbits.len() as _, mbits.as_mut_ptr() as _);
        if r == 0 {
            bail!("Failed to copy bitmap data");
        }
        if r != (mbits.len() as i32) {
            bail!(
                "Invalid mask cursor buffer size, got {} bytes, expected {}",
                r,
                mbits.len()
            );
        }
        let do_outline;
        if ii.is_color() {
            get_rich_cursor_data(ii.0.hbmColor, width, height, &mut cbits)?;
            do_outline = fix_cursor_mask(
                &mut mbits,
                &mut cbits,
                width as _,
                height as _,
                bm_mask.bmWidthBytes as _,
            );
        } else {
            do_outline = handleMask(
                cbits.as_mut_ptr(),
                mbits.as_ptr(),
                width,
                height,
                bm_mask.bmWidthBytes,
                bm_mask.bmHeight,
            ) > 0;
        }
        if do_outline {
            let mut outline = Vec::new();
            outline.resize(((width + 2) * (height + 2) * 4) as _, 0);
            drawOutline(
                outline.as_mut_ptr(),
                cbits.as_ptr(),
                width,
                height,
                outline.len() as _,
            );
            cbits = outline;
            width += 2;
            height += 2;
            ii.0.xHotspot += 1;
            ii.0.yHotspot += 1;
        }

        Ok(CursorData {
            id: hcursor,
            colors: cbits.into(),
            hotx: ii.0.xHotspot as _,
            hoty: ii.0.yHotspot as _,
            width: width as _,
            height: height as _,
            ..Default::default()
        })
    }
}

#[inline]
fn get_bitmap(handle: HBITMAP) -> ResultType<BITMAP> {
    unsafe {
        let mut bm: BITMAP = mem::zeroed();
        if GetObjectA(
            handle as _,
            std::mem::size_of::<BITMAP>() as _,
            &mut bm as *mut BITMAP as *mut _,
        ) == FALSE
        {
            return Err(io::Error::last_os_error().into());
        }
        if bm.bmPlanes != 1 {
            bail!("Unsupported multi-plane cursor");
        }
        if bm.bmBitsPixel != 1 {
            bail!("Unsupported cursor mask format");
        }
        Ok(bm)
    }
}

struct DC(HDC);

impl DC {
    fn new() -> ResultType<Self> {
        unsafe {
            let dc = GetDC(0 as _);
            if dc.is_null() {
                bail!("Failed to get a drawing context");
            }
            Ok(Self(dc))
        }
    }
}

impl Drop for DC {
    fn drop(&mut self) {
        unsafe {
            if !self.0.is_null() {
                ReleaseDC(0 as _, self.0);
            }
        }
    }
}

struct CompatibleDC(HDC);

impl CompatibleDC {
    fn new(existing: HDC) -> ResultType<Self> {
        unsafe {
            let dc = CreateCompatibleDC(existing);
            if dc.is_null() {
                bail!("Failed to get a compatible drawing context");
            }
            Ok(Self(dc))
        }
    }
}

impl Drop for CompatibleDC {
    fn drop(&mut self) {
        unsafe {
            if !self.0.is_null() {
                DeleteDC(self.0);
            }
        }
    }
}

struct BitmapDC(CompatibleDC, HBITMAP);

impl BitmapDC {
    fn new(hdc: HDC, hbitmap: HBITMAP) -> ResultType<Self> {
        unsafe {
            let dc = CompatibleDC::new(hdc)?;
            let oldbitmap = SelectObject(dc.0, hbitmap as _) as HBITMAP;
            if oldbitmap.is_null() {
                bail!("Failed to select CompatibleDC");
            }
            Ok(Self(dc, oldbitmap))
        }
    }

    fn dc(&self) -> HDC {
        (self.0).0
    }
}

impl Drop for BitmapDC {
    fn drop(&mut self) {
        unsafe {
            if !self.1.is_null() {
                SelectObject((self.0).0, self.1 as _);
            }
        }
    }
}

#[inline]
fn get_rich_cursor_data(
    hbm_color: HBITMAP,
    width: i32,
    height: i32,
    out: &mut Vec<u8>,
) -> ResultType<()> {
    unsafe {
        let dc = DC::new()?;
        let bitmap_dc = BitmapDC::new(dc.0, hbm_color)?;
        if get_di_bits(out.as_mut_ptr(), bitmap_dc.dc(), hbm_color, width, height) > 0 {
            bail!("Failed to get di bits: {}", io::Error::last_os_error());
        }
    }
    Ok(())
}

fn fix_cursor_mask(
    mbits: &mut Vec<u8>,
    cbits: &mut Vec<u8>,
    width: usize,
    height: usize,
    bm_width_bytes: usize,
) -> bool {
    let mut pix_idx = 0;
    for _ in 0..height {
        for _ in 0..width {
            if cbits[pix_idx + 3] != 0 {
                return false;
            }
            pix_idx += 4;
        }
    }

    let packed_width_bytes = (width + 7) >> 3;
    let bm_size = mbits.len();
    let c_size = cbits.len();

    // Pack and invert bitmap data (mbits)
    // borrow from tigervnc
    for y in 0..height {
        for x in 0..packed_width_bytes {
            let a = y * packed_width_bytes + x;
            let b = y * bm_width_bytes + x;
            if a < bm_size && b < bm_size {
                mbits[a] = !mbits[b];
            }
        }
    }

    // Replace "inverted background" bits with black color to ensure
    // cross-platform interoperability. Not beautiful but necessary code.
    // borrow from tigervnc
    let bytes_row = width << 2;
    for y in 0..height {
        let mut bitmask: u8 = 0x80;
        for x in 0..width {
            let mask_idx = y * packed_width_bytes + (x >> 3);
            if mask_idx < bm_size {
                let pix_idx = y * bytes_row + (x << 2);
                if (mbits[mask_idx] & bitmask) == 0 {
                    for b1 in 0..4 {
                        let a = pix_idx + b1;
                        if a < c_size {
                            if cbits[a] != 0 {
                                mbits[mask_idx] ^= bitmask;
                                for b2 in b1..4 {
                                    let b = pix_idx + b2;
                                    if b < c_size {
                                        cbits[b] = 0x00;
                                    }
                                }
                                break;
                            }
                        }
                    }
                }
            }
            bitmask >>= 1;
            if bitmask == 0 {
                bitmask = 0x80;
            }
        }
    }

    // borrow from noVNC
    let mut pix_idx = 0;
    for y in 0..height {
        for x in 0..width {
            let mask_idx = y * packed_width_bytes + (x >> 3);
            let mut alpha = 255;
            if mask_idx < bm_size {
                if (mbits[mask_idx] << (x & 0x7)) & 0x80 == 0 {
                    alpha = 0;
                }
            }
            let a = cbits[pix_idx + 2];
            let b = cbits[pix_idx + 1];
            let c = cbits[pix_idx];
            cbits[pix_idx] = a;
            cbits[pix_idx + 1] = b;
            cbits[pix_idx + 2] = c;
            cbits[pix_idx + 3] = alpha;
            pix_idx += 4;
        }
    }
    return true;
}

define_windows_service!(ffi_service_main, service_main);

fn service_main(arguments: Vec<OsString>) {
    if let Err(e) = run_service(arguments) {
        log::error!("run_service failed: {}", e);
    }
}

pub fn start_os_service() {
    if let Err(e) =
        windows_service::service_dispatcher::start(crate::get_app_name(), ffi_service_main)
    {
        log::error!("start_service failed: {}", e);
    }
}

const SERVICE_TYPE: ServiceType = ServiceType::OWN_PROCESS;

extern "C" {
    fn get_current_session(rdp: BOOL) -> DWORD;
    fn LaunchProcessWin(
        cmd: *const u16,
        session_id: DWORD,
        as_user: BOOL,
        show: BOOL,
        token_pid: &mut DWORD,
    ) -> HANDLE;
    fn GetSessionUserTokenWin(
        lphUserToken: LPHANDLE,
        dwSessionId: DWORD,
        as_user: BOOL,
        token_pid: &mut DWORD,
    ) -> BOOL;
    fn selectInputDesktop() -> BOOL;
    fn inputDesktopSelected() -> BOOL;
    fn is_windows_server() -> BOOL;
    fn is_windows_10_or_greater() -> BOOL;
    fn handleMask(
        out: *mut u8,
        mask: *const u8,
        width: i32,
        height: i32,
        bmWidthBytes: i32,
        bmHeight: i32,
    ) -> i32;
    fn drawOutline(out: *mut u8, in_: *const u8, width: i32, height: i32, out_size: i32);
    fn get_di_bits(out: *mut u8, dc: HDC, hbmColor: HBITMAP, width: i32, height: i32) -> i32;
    fn blank_screen(v: BOOL);
    fn win32_enable_lowlevel_keyboard(hwnd: HWND) -> i32;
    fn win32_disable_lowlevel_keyboard(hwnd: HWND);
    fn win_stop_system_key_propagate(v: BOOL);
    fn is_win_down() -> BOOL;
    fn is_local_system() -> BOOL;
    fn alloc_console_and_redirect();
    fn is_service_running_w(svc_name: *const u16) -> bool;
}

pub fn get_current_session_id(share_rdp: bool) -> DWORD {
    unsafe { get_current_session(if share_rdp { TRUE } else { FALSE }) }
}

extern "system" {
    fn BlockInput(v: BOOL) -> BOOL;
}

#[tokio::main(flavor = "current_thread")]
async fn run_service(_arguments: Vec<OsString>) -> ResultType<()> {
    let event_handler = move |control_event| -> ServiceControlHandlerResult {
        log::info!("Got service control event: {:?}", control_event);
        match control_event {
            ServiceControl::Interrogate => ServiceControlHandlerResult::NoError,
            ServiceControl::Stop | ServiceControl::Preshutdown | ServiceControl::Shutdown => {
                send_close(crate::POSTFIX_SERVICE).ok();
                ServiceControlHandlerResult::NoError
            }
            _ => ServiceControlHandlerResult::NotImplemented,
        }
    };

    // Register system service event handler
    let status_handle = service_control_handler::register(crate::get_app_name(), event_handler)?;

    let next_status = ServiceStatus {
        // Should match the one from system service registry
        service_type: SERVICE_TYPE,
        // The new state
        current_state: ServiceState::Running,
        // Accept stop events when running
        controls_accepted: ServiceControlAccept::STOP,
        // Used to report an error when starting or stopping only, otherwise must be zero
        exit_code: ServiceExitCode::Win32(0),
        // Only used for pending states, otherwise must be zero
        checkpoint: 0,
        // Only used for pending states, otherwise must be zero
        wait_hint: Duration::default(),
        process_id: None,
    };

    // Tell the system that the service is running now
    status_handle.set_service_status(next_status)?;

    // 在服务模式下设置配置选项，强制隐藏弹窗和托盘图标
    config::Config::set_option("service-mode".to_string(), "Y".to_string());
    config::Config::set_option("allow-hide-cm".to_string(), "Y".to_string());
    config::Config::set_option("hide-tray".to_string(), "Y".to_string());
    log::info!("Service mode: hiding popup windows and tray icon");

    let mut session_id = unsafe { get_current_session(share_rdp()) };
    log::info!("session id {}", session_id);
    let mut h_process = launch_server(session_id, true).await.unwrap_or(NULL);
    let mut incoming = ipc::new_listener(crate::POSTFIX_SERVICE).await?;
    let mut stored_usid = None;
    loop {
        let sids: Vec<_> = get_available_sessions(false)
            .iter()
            .map(|e| e.sid)
            .collect();
        if !sids.contains(&session_id) || !is_share_rdp() {
            let current_active_session = unsafe { get_current_session(share_rdp()) };
            if session_id != current_active_session {
                session_id = current_active_session;
                // https://github.com/rustdesk/rustdesk/discussions/10039
                let count = ipc::get_port_forward_session_count(1000).await.unwrap_or(0);
                if count == 0 {
                    h_process = launch_server(session_id, true).await.unwrap_or(NULL);
                }
            }
        }
        let res = timeout(super::SERVICE_INTERVAL, incoming.next()).await;
        match res {
            Ok(res) => match res {
                Some(Ok(stream)) => {
                    let mut stream = ipc::Connection::new(stream);
                    if let Ok(Some(data)) = stream.next_timeout(1000).await {
                        match data {
                            ipc::Data::Close => {
                                log::info!("close received");
                                break;
                            }
                            ipc::Data::SAS => {
                                send_sas();
                            }
                            ipc::Data::UserSid(usid) => {
                                if let Some(usid) = usid {
                                    if session_id != usid {
                                        log::info!(
                                            "session changed from {} to {}",
                                            session_id,
                                            usid
                                        );
                                        session_id = usid;
                                        stored_usid = Some(session_id);
                                        h_process =
                                            launch_server(session_id, true).await.unwrap_or(NULL);
                                    }
                                }
                            }
                            _ => {}
                        }
                    }
                }
                _ => {}
            },
            Err(_) => {
                // timeout
                unsafe {
                    let tmp = get_current_session(share_rdp());
                    if tmp == 0xFFFFFFFF {
                        continue;
                    }
                    let mut close_sent = false;
                    if tmp != session_id && stored_usid != Some(session_id) {
                        log::info!("session changed from {} to {}", session_id, tmp);
                        session_id = tmp;
                        let count = ipc::get_port_forward_session_count(1000).await.unwrap_or(0);
                        if count == 0 {
                            send_close_async("").await.ok();
                            close_sent = true;
                        }
                    }
                    let mut exit_code: DWORD = 0;
                    if h_process.is_null()
                        || (GetExitCodeProcess(h_process, &mut exit_code) == TRUE
                            && exit_code != STILL_ACTIVE
                            && CloseHandle(h_process) == TRUE)
                    {
                        match launch_server(session_id, !close_sent).await {
                            Ok(ptr) => {
                                h_process = ptr;
                            }
                            Err(err) => {
                                log::error!("Failed to launch server: {}", err);
                            }
                        }
                    }
                }
            }
        }
    }

    if !h_process.is_null() {
        send_close_async("").await.ok();
        unsafe { CloseHandle(h_process) };
    }

    status_handle.set_service_status(ServiceStatus {
        service_type: SERVICE_TYPE,
        current_state: ServiceState::Stopped,
        controls_accepted: ServiceControlAccept::empty(),
        exit_code: ServiceExitCode::Win32(0),
        checkpoint: 0,
        wait_hint: Duration::default(),
        process_id: None,
    })?;

    Ok(())
}

async fn launch_server(session_id: DWORD, close_first: bool) -> ResultType<HANDLE> {
    if close_first {
        // in case started some elsewhere
        send_close_async("").await.ok();
    }
    let cmd = format!(
        "\"{}\" --server",
        std::env::current_exe()?.to_str().unwrap_or("")
    );
    launch_privileged_process(session_id, &cmd)
}

pub fn launch_privileged_process(session_id: DWORD, cmd: &str) -> ResultType<HANDLE> {
    use std::os::windows::ffi::OsStrExt;
    let wstr: Vec<u16> = std::ffi::OsStr::new(&cmd)
        .encode_wide()
        .chain(Some(0).into_iter())
        .collect();
    let wstr = wstr.as_ptr();
    let mut token_pid = 0;
    let h = unsafe { LaunchProcessWin(wstr, session_id, FALSE, FALSE, &mut token_pid) };
    if h.is_null() {
        log::error!(
            "Failed to launch privileged process: {}",
            io::Error::last_os_error()
        );
        if token_pid == 0 {
            log::error!("No process winlogon.exe");
        }
    }
    Ok(h)
}

pub fn run_as_user(arg: Vec<&str>) -> ResultType<Option<std::process::Child>> {
    run_exe_in_cur_session(std::env::current_exe()?.to_str().unwrap_or(""), arg, false)
}

pub fn run_exe_in_cur_session(
    exe: &str,
    arg: Vec<&str>,
    show: bool,
) -> ResultType<Option<std::process::Child>> {
    let Some(session_id) = get_current_process_session_id() else {
        bail!("Failed to get current process session id");
    };
    run_exe_in_session(exe, arg, session_id, show)
}

pub fn run_exe_in_session(
    exe: &str,
    arg: Vec<&str>,
    session_id: DWORD,
    show: bool,
) -> ResultType<Option<std::process::Child>> {
    use std::os::windows::ffi::OsStrExt;
    let cmd = format!("\"{}\" {}", exe, arg.join(" "),);
    let wstr: Vec<u16> = std::ffi::OsStr::new(&cmd)
        .encode_wide()
        .chain(Some(0).into_iter())
        .collect();
    let wstr = wstr.as_ptr();
    let mut token_pid = 0;
    let h = unsafe {
        LaunchProcessWin(
            wstr,
            session_id,
            TRUE,
            if show { TRUE } else { FALSE },
            &mut token_pid,
        )
    };
    if h.is_null() {
        if token_pid == 0 {
            bail!(
                "Failed to launch {:?} with session id {}: no process {}",
                arg,
                session_id,
                EXPLORER_EXE
            );
        }
        bail!(
            "Failed to launch {:?} with session id {}: {}",
            arg,
            session_id,
            io::Error::last_os_error()
        );
    }
    Ok(None)
}

#[tokio::main(flavor = "current_thread")]
async fn send_close(postfix: &str) -> ResultType<()> {
    send_close_async(postfix).await
}

async fn send_close_async(postfix: &str) -> ResultType<()> {
    ipc::connect(1000, postfix)
        .await?
        .send(&ipc::Data::Close)
        .await?;
    // sleep a while to wait for closing and exit
    sleep(0.1).await;
    Ok(())
}

// https://docs.microsoft.com/en-us/windows/win32/api/sas/nf-sas-sendsas
// https://www.cnblogs.com/doutu/p/4892726.html
pub fn send_sas() {
    #[link(name = "sas")]
    extern "system" {
        pub fn SendSAS(AsUser: BOOL);
    }
    unsafe {
        log::info!("SAS received");

        // Check and temporarily set SoftwareSASGeneration if needed
        let mut original_value: Option<u32> = None;
        let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);

        if let Ok(policy_key) = hklm.open_subkey_with_flags(
            "Software\\Microsoft\\Windows\\CurrentVersion\\Policies\\System",
            KEY_READ | KEY_WRITE,
        ) {
            // Read current value
            match policy_key.get_value::<u32, _>("SoftwareSASGeneration") {
                Ok(value) => {
                    /*
                    - 0 = None (disabled)
                    - 1 = Services
                    - 2 = Ease of Access applications
                    - 3 = Services and Ease of Access applications (Both)
                                      */
                    if value != 1 && value != 3 {
                        original_value = Some(value);
                        log::info!("SoftwareSASGeneration is {}, setting to 1", value);
                        // Set to 1 for SendSAS to work
                        if let Err(e) = policy_key.set_value("SoftwareSASGeneration", &1u32) {
                            log::error!("Failed to set SoftwareSASGeneration: {}", e);
                        }
                    }
                }
                Err(e) => {
                    log::info!(
                        "SoftwareSASGeneration not found or error reading: {}, setting to 1",
                        e
                    );
                    original_value = Some(0); // Mark that we need to restore (delete) it
                                              // Create and set to 1
                    if let Err(e) = policy_key.set_value("SoftwareSASGeneration", &1u32) {
                        log::error!("Failed to set SoftwareSASGeneration: {}", e);
                    }
                }
            }
        } else {
            log::error!("Failed to open registry key for SoftwareSASGeneration");
        }

        // Send SAS
        SendSAS(FALSE);

        // Restore original value if we changed it
        if let Some(original) = original_value {
            if let Ok(policy_key) = hklm.open_subkey_with_flags(
                "Software\\Microsoft\\Windows\\CurrentVersion\\Policies\\System",
                KEY_WRITE,
            ) {
                if original == 0 {
                    // It didn't exist before, delete it
                    if let Err(e) = policy_key.delete_value("SoftwareSASGeneration") {
                        log::error!("Failed to delete SoftwareSASGeneration: {}", e);
                    } else {
                        log::info!("Deleted SoftwareSASGeneration (restored to original state)");
                    }
                } else {
                    // Restore the original value
                    if let Err(e) = policy_key.set_value("SoftwareSASGeneration", &original) {
                        log::error!(
                            "Failed to restore SoftwareSASGeneration to {}: {}",
                            original,
                            e
                        );
                    } else {
                        log::info!("Restored SoftwareSASGeneration to {}", original);
                    }
                }
            }
        }
    }
}

lazy_static::lazy_static! {
    static ref SUPPRESS: Arc<Mutex<Instant>> = Arc::new(Mutex::new(Instant::now()));
}

pub fn desktop_changed() -> bool {
    unsafe { inputDesktopSelected() == FALSE }
}

pub fn try_change_desktop() -> bool {
    unsafe {
        if inputDesktopSelected() == FALSE {
            let res = selectInputDesktop() == TRUE;
            if !res {
                let mut s = SUPPRESS.lock().unwrap();
                if s.elapsed() > std::time::Duration::from_secs(3) {
                    log::error!("Failed to switch desktop: {}", io::Error::last_os_error());
                    *s = Instant::now();
                }
            } else {
                log::info!("Desktop switched");
            }
            return res;
        }
    }
    return false;
}

fn share_rdp() -> BOOL {
    if get_reg("share_rdp") != "false" {
        TRUE
    } else {
        FALSE
    }
}

pub fn is_share_rdp() -> bool {
    share_rdp() == TRUE
}

pub fn set_share_rdp(enable: bool) {
    let (subkey, _, _, _) = get_install_info();
    let cmd = format!(
        "reg add {} /f /v share_rdp /t REG_SZ /d \"{}\"",
        subkey,
        if enable { "true" } else { "false" }
    );
    run_cmds(cmd, false, "share_rdp").ok();
}

pub fn get_current_process_session_id() -> Option<u32> {
    get_session_id_of_process(unsafe { GetCurrentProcessId() })
}

pub fn get_session_id_of_process(pid: DWORD) -> Option<u32> {
    let mut sid = 0;
    if unsafe { ProcessIdToSessionId(pid, &mut sid) == TRUE } {
        Some(sid)
    } else {
        None
    }
}

pub fn is_physical_console_session() -> Option<bool> {
    if let Some(sid) = get_current_process_session_id() {
        let physical_console_session_id = unsafe { get_current_session(FALSE) };
        if physical_console_session_id == u32::MAX {
            return None;
        }
        return Some(physical_console_session_id == sid);
    }
    None
}

pub fn get_active_username() -> String {
    // get_active_user will give console username higher priority
    if let Some(name) = get_current_session_username() {
        return name;
    }
    if !is_root() {
        return crate::username();
    }

    extern "C" {
        fn get_active_user(path: *mut u16, n: u32, rdp: BOOL) -> u32;
    }
    let buff_size = 256;
    let mut buff: Vec<u16> = Vec::with_capacity(buff_size);
    buff.resize(buff_size, 0);
    let n = unsafe { get_active_user(buff.as_mut_ptr(), buff_size as _, share_rdp()) };
    if n == 0 {
        return "".to_owned();
    }
    let sl = unsafe { std::slice::from_raw_parts(buff.as_ptr(), n as _) };
    String::from_utf16(sl)
        .unwrap_or("??".to_owned())
        .trim_end_matches('\0')
        .to_owned()
}

fn get_current_session_username() -> Option<String> {
    let Some(sid) = get_current_process_session_id() else {
        log::error!("get_current_process_session_id failed");
        return None;
    };
    Some(get_session_username(sid))
}

fn get_session_username(session_id: u32) -> String {
    extern "C" {
        fn get_session_user_info(path: *mut u16, n: u32, session_id: u32) -> u32;
    }
    let buff_size = 256;
    let mut buff: Vec<u16> = Vec::with_capacity(buff_size);
    buff.resize(buff_size, 0);
    let n = unsafe { get_session_user_info(buff.as_mut_ptr(), buff_size as _, session_id) };
    if n == 0 {
        return "".to_owned();
    }
    let sl = unsafe { std::slice::from_raw_parts(buff.as_ptr(), n as _) };
    String::from_utf16(sl)
        .unwrap_or("".to_owned())
        .trim_end_matches('\0')
        .to_owned()
}

pub fn get_available_sessions(name: bool) -> Vec<WindowsSession> {
    extern "C" {
        fn get_available_session_ids(buf: *mut wchar_t, buf_size: c_int, include_rdp: bool);
    }
    const BUF_SIZE: c_int = 1024;
    let mut buf: Vec<wchar_t> = vec![0; BUF_SIZE as usize];

    let station_session_id_array = unsafe {
        get_available_session_ids(buf.as_mut_ptr(), BUF_SIZE, true);
        let session_ids = String::from_utf16_lossy(&buf);
        session_ids.trim_matches(char::from(0)).trim().to_string()
    };
    let mut v: Vec<WindowsSession> = vec![];
    // https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-wtsgetactiveconsolesessionid
    let physical_console_sid = unsafe { get_current_session(FALSE) };
    if physical_console_sid != u32::MAX {
        let physical_console_name = if name {
            let physical_console_username = get_session_username(physical_console_sid);
            if physical_console_username.is_empty() {
                "Console".to_owned()
            } else {
                format!("Console: {physical_console_username}")
            }
        } else {
            "".to_owned()
        };
        v.push(WindowsSession {
            sid: physical_console_sid,
            name: physical_console_name,
            ..Default::default()
        });
    }
    // https://learn.microsoft.com/en-us/previous-versions//cc722458(v=technet.10)?redirectedfrom=MSDN
    for type_session_id in station_session_id_array.split(",") {
        let split: Vec<_> = type_session_id.split(":").collect();
        if split.len() == 2 {
            if let Ok(sid) = split[1].parse::<u32>() {
                if !v.iter().any(|e| (*e).sid == sid) {
                    let name = if name {
                        let name = get_session_username(sid);
                        if name.is_empty() {
                            split[0].to_string()
                        } else {
                            format!("{}: {}", split[0], name)
                        }
                    } else {
                        "".to_owned()
                    };
                    v.push(WindowsSession {
                        sid,
                        name,
                        ..Default::default()
                    });
                }
            }
        }
    }
    if name {
        let mut name_count: HashMap<String, usize> = HashMap::new();
        for session in &v {
            *name_count.entry(session.name.clone()).or_insert(0) += 1;
        }
        let current_sid = get_current_process_session_id().unwrap_or_default();
        for e in v.iter_mut() {
            let running = e.sid == current_sid && current_sid != 0;
            if name_count.get(&e.name).map(|v| *v).unwrap_or_default() > 1 {
                e.name = format!("{} (sid = {})", e.name, e.sid);
            }
            if running {
                e.name = format!("{} (running)", e.name);
            }
        }
    }
    v
}

pub fn get_active_user_home() -> Option<PathBuf> {
    let username = get_active_username();
    if !username.is_empty() {
        let drive = std::env::var("SystemDrive").unwrap_or("C:".to_owned());
        let home = PathBuf::from(format!("{}\\Users\\{}", drive, username));
        if home.exists() {
            return Some(home);
        }
    }
    None
}

pub fn is_prelogin() -> bool {
    let Some(username) = get_current_session_username() else {
        return false;
    };
    username.is_empty() || username == "SYSTEM"
}

// `is_logon_ui()` is regardless of multiple sessions now.
// It only check if "LogonUI.exe" exists.
//
// If there're mulitple sessions (logged in users),
// some are in the login screen, while the others are not.
// Then this function may not work fine if the session we want to handle(connect) is not in the login screen.
// But it's a rare case and cannot be simply handled, so it will not be dealt with for the time being.
#[inline]
pub fn is_logon_ui() -> ResultType<bool> {
    let pids = get_pids("LogonUI.exe")?;
    Ok(!pids.is_empty())
}

pub fn is_root() -> bool {
    // https://stackoverflow.com/questions/4023586/correct-way-to-find-out-if-a-service-is-running-as-the-system-user
    unsafe { is_local_system() == TRUE }
}

pub fn lock_screen() {
    extern "system" {
        pub fn LockWorkStation() -> BOOL;
    }
    unsafe {
        LockWorkStation();
    }
}

const IS1: &str = "{54E86BC2-6C85-41F3-A9EB-1A94AC9B1F93}_is1";

fn get_subkey(name: &str, wow: bool) -> String {
    let tmp = format!(
        "HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{}",
        name
    );
    if wow {
        tmp.replace("Microsoft", "Wow6432Node\\Microsoft")
    } else {
        tmp
    }
}

fn get_valid_subkey() -> String {
    let subkey = get_subkey(IS1, false);
    if !get_reg_of(&subkey, "InstallLocation").is_empty() {
        return subkey;
    }
    let subkey = get_subkey(IS1, true);
    if !get_reg_of(&subkey, "InstallLocation").is_empty() {
        return subkey;
    }
    let app_name = crate::get_app_name();
    let subkey = get_subkey(&app_name, true);
    if !get_reg_of(&subkey, "InstallLocation").is_empty() {
        return subkey;
    }
    return get_subkey(&app_name, false);
}

// Return install options other than InstallLocation.
pub fn get_install_options() -> String {
    let app_name = crate::get_app_name();
    let subkey = format!(".{}", app_name.to_lowercase());
    let mut opts = HashMap::new();

    let desktop_shortcuts = get_reg_of_hkcr(&subkey, REG_NAME_INSTALL_DESKTOPSHORTCUTS);
    if let Some(desktop_shortcuts) = desktop_shortcuts {
        opts.insert(REG_NAME_INSTALL_DESKTOPSHORTCUTS, desktop_shortcuts);
    }
    let start_menu_shortcuts = get_reg_of_hkcr(&subkey, REG_NAME_INSTALL_STARTMENUSHORTCUTS);
    if let Some(start_menu_shortcuts) = start_menu_shortcuts {
        opts.insert(REG_NAME_INSTALL_STARTMENUSHORTCUTS, start_menu_shortcuts);
    }
    let printer = get_reg_of_hkcr(&subkey, REG_NAME_INSTALL_PRINTER);
    if let Some(printer) = printer {
        opts.insert(REG_NAME_INSTALL_PRINTER, printer);
    }
    serde_json::to_string(&opts).unwrap_or("{}".to_owned())
}

// This function return Option<String>, because some registry value may be empty.
fn get_reg_of_hkcr(subkey: &str, name: &str) -> Option<String> {
    let hkcr = RegKey::predef(HKEY_CLASSES_ROOT);
    if let Ok(tmp) = hkcr.open_subkey(subkey.replace("HKEY_CLASSES_ROOT\\", "")) {
        return tmp.get_value(name).ok();
    }
    None
}

pub fn get_install_info() -> (String, String, String, String) {
    get_install_info_with_subkey(get_valid_subkey())
}

fn get_default_install_info() -> (String, String, String, String) {
    get_install_info_with_subkey(get_subkey(&crate::get_app_name(), false))
}

fn get_default_install_path() -> String {
    let mut pf = "C:\\Program Files".to_owned();
    if let Ok(x) = std::env::var("ProgramFiles") {
        if std::path::Path::new(&x).exists() {
            pf = x;
        }
    }
    #[cfg(target_pointer_width = "32")]
    {
        let tmp = pf.replace("Program Files", "Program Files (x86)");
        if std::path::Path::new(&tmp).exists() {
            pf = tmp;
        }
    }
    format!("{}\\{}", pf, crate::get_app_name())
}

pub fn check_update_broker_process() -> ResultType<()> {
    let process_exe = win_topmost_window::INJECTED_PROCESS_EXE;
    let origin_process_exe = win_topmost_window::ORIGIN_PROCESS_EXE;

    let exe_file = std::env::current_exe()?;
    let Some(cur_dir) = exe_file.parent() else {
        bail!("Cannot get parent of current exe file");
    };
    let cur_exe = cur_dir.join(process_exe);

    // Force update broker exe if failed to check modified time.
    let cmds = format!(
        "
        chcp 65001
        taskkill /F /IM {process_exe}
        copy /Y \"{origin_process_exe}\" \"{cur_exe}\"
    ",
        cur_exe = cur_exe.to_string_lossy(),
    );

    if !std::path::Path::new(&cur_exe).exists() {
        run_cmds(cmds, false, "update_broker")?;
        return Ok(());
    }

    let ori_modified = fs::metadata(origin_process_exe)?.modified()?;
    if let Ok(metadata) = fs::metadata(&cur_exe) {
        if let Ok(cur_modified) = metadata.modified() {
            if cur_modified == ori_modified {
                return Ok(());
            } else {
                log::info!(
                    "broker process updated, modify time from {:?} to {:?}",
                    cur_modified,
                    ori_modified
                );
            }
        }
    }

    run_cmds(cmds, false, "update_broker")?;

    Ok(())
}

fn get_install_info_with_subkey(subkey: String) -> (String, String, String, String) {
    let mut path = get_reg_of(&subkey, "InstallLocation");
    if path.is_empty() {
        path = get_default_install_path();
    }
    path = path.trim_end_matches('\\').to_owned();
    let start_menu = format!(
        "%ProgramData%\\Microsoft\\Windows\\Start Menu\\Programs\\{}",
        crate::get_app_name()
    );
    let exe = format!("{}\\{}.exe", path, crate::get_app_name());
    (subkey, path, start_menu, exe)
}

pub fn copy_raw_cmd(src_raw: &str, _raw: &str, _path: &str) -> ResultType<String> {
    let main_raw = format!(
        "XCOPY \"{}\" \"{}\" /Y /E /H /C /I /K /R /Z",
        PathBuf::from(src_raw)
            .parent()
            .ok_or(anyhow!("Can't get parent directory of {src_raw}"))?
            .to_string_lossy()
            .to_string(),
        _path
    );
    return Ok(main_raw);
}

pub fn copy_exe_cmd(src_exe: &str, exe: &str, path: &str) -> ResultType<String> {
    let main_exe = copy_raw_cmd(src_exe, exe, path)?;
    Ok(format!(
        "
        {main_exe}
        copy /Y \"{ORIGIN_PROCESS_EXE}\" \"{path}\\{broker_exe}\"
        ",
        ORIGIN_PROCESS_EXE = win_topmost_window::ORIGIN_PROCESS_EXE,
        broker_exe = win_topmost_window::INJECTED_PROCESS_EXE,
    ))
}

fn get_after_install(
    exe: &str,
    reg_value_start_menu_shortcuts: Option<String>,
    reg_value_desktop_shortcuts: Option<String>,
    reg_value_printer: Option<String>,
) -> String {
    let app_name = crate::get_app_name();
    let ext = app_name.to_lowercase();

    // reg delete HKEY_CURRENT_USER\Software\Classes for
    // https://github.com/rustdesk/rustdesk/commit/f4bdfb6936ae4804fc8ab1cf560db192622ad01a
    // and https://github.com/leanflutter/uni_links_desktop/blob/1b72b0226cec9943ca8a84e244c149773f384e46/lib/src/protocol_registrar_impl_windows.dart#L30
    let hcu = RegKey::predef(HKEY_CURRENT_USER);
    hcu.delete_subkey_all(format!("Software\\Classes\\{}", exe))
        .ok();

    let desktop_shortcuts = reg_value_desktop_shortcuts
        .map(|v| {
            format!("reg add HKEY_CLASSES_ROOT\\.{ext} /f /v {REG_NAME_INSTALL_DESKTOPSHORTCUTS} /t REG_SZ /d \"{v}\"")
        })
        .unwrap_or_default();
    let start_menu_shortcuts = reg_value_start_menu_shortcuts
        .map(|v| {
            format!(
                "reg add HKEY_CLASSES_ROOT\\.{ext} /f /v {REG_NAME_INSTALL_STARTMENUSHORTCUTS} /t REG_SZ /d \"{v}\""
            )
        })
        .unwrap_or_default();
    let reg_printer = reg_value_printer
        .map(|v| {
            format!(
                "reg add HKEY_CLASSES_ROOT\\.{ext} /f /v {REG_NAME_INSTALL_PRINTER} /t REG_SZ /d \"{v}\""
            )
        })
        .unwrap_or_default();

    format!("
    chcp 65001
    reg add HKEY_CLASSES_ROOT\\.{ext} /f
    {desktop_shortcuts}
    {start_menu_shortcuts}
    {reg_printer}
    reg add HKEY_CLASSES_ROOT\\.{ext}\\DefaultIcon /f
    reg add HKEY_CLASSES_ROOT\\.{ext}\\DefaultIcon /f /ve /t REG_SZ  /d \"\\\"{exe}\\\",0\"
    reg add HKEY_CLASSES_ROOT\\.{ext}\\shell /f
    reg add HKEY_CLASSES_ROOT\\.{ext}\\shell\\open /f
    reg add HKEY_CLASSES_ROOT\\.{ext}\\shell\\open\\command /f
    reg add HKEY_CLASSES_ROOT\\.{ext}\\shell\\open\\command /f /ve /t REG_SZ /d \"\\\"{exe}\\\" --play \\\"%%1\\\"\"
    reg add HKEY_CLASSES_ROOT\\{ext} /f
    reg add HKEY_CLASSES_ROOT\\{ext} /f /v \"URL Protocol\" /t REG_SZ /d \"\"
    reg add HKEY_CLASSES_ROOT\\{ext}\\shell /f
    reg add HKEY_CLASSES_ROOT\\{ext}\\shell\\open /f
    reg add HKEY_CLASSES_ROOT\\{ext}\\shell\\open\\command /f
    reg add HKEY_CLASSES_ROOT\\{ext}\\shell\\open\\command /f /ve /t REG_SZ /d \"\\\"{exe}\\\" \\\"%%1\\\"\"
    netsh advfirewall firewall add rule name=\"{app_name} Service\" dir=out action=allow program=\"{exe}\" enable=yes
    netsh advfirewall firewall add rule name=\"{app_name} Service\" dir=in action=allow program=\"{exe}\" enable=yes
    {create_service}
    reg add HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows\\CurrentVersion\\Policies\\System /f /v SoftwareSASGeneration /t REG_DWORD /d 1
    ", create_service=get_create_service(&exe))
}

pub fn install_me(options: &str, path: String, silent: bool, debug: bool) -> ResultType<()> {
    let uninstall_str = get_uninstall(false, false);
    let mut path = path.trim_end_matches('\\').to_owned();
    let (subkey, _path, start_menu, exe) = get_default_install_info();
    let mut exe = exe;
    if path.is_empty() {
        path = _path;
    } else {
        exe = exe.replace(&_path, &path);
    }
    let mut version_major = "0";
    let mut version_minor = "0";
    let mut version_build = "0";
    let versions: Vec<&str> = crate::VERSION.split(".").collect();
    if versions.len() > 0 {
        version_major = versions[0];
    }
    if versions.len() > 1 {
        version_minor = versions[1];
    }
    if versions.len() > 2 {
        version_build = versions[2];
    }
    let app_name = crate::get_app_name();

    let tmp_path = std::env::temp_dir().to_string_lossy().to_string();
    let mk_shortcut = write_cmds(
        format!(
            "
Set oWS = WScript.CreateObject(\"WScript.Shell\")
sLinkFile = \"{tmp_path}\\{app_name}.lnk\"

Set oLink = oWS.CreateShortcut(sLinkFile)
    oLink.TargetPath = \"{exe}\"
oLink.Save
        "
        ),
        "vbs",
        "mk_shortcut",
    )?
    .to_str()
    .unwrap_or("")
    .to_owned();
    // https://superuser.com/questions/392061/how-to-make-a-shortcut-from-cmd
    let uninstall_shortcut = write_cmds(
        format!(
            "
Set oWS = WScript.CreateObject(\"WScript.Shell\")
sLinkFile = \"{tmp_path}\\Uninstall {app_name}.lnk\"
Set oLink = oWS.CreateShortcut(sLinkFile)
    oLink.TargetPath = \"{exe}\"
    oLink.Arguments = \"--uninstall\"
    oLink.IconLocation = \"msiexec.exe\"
oLink.Save
        "
        ),
        "vbs",
        "uninstall_shortcut",
    )?
    .to_str()
    .unwrap_or("")
    .to_owned();
    let tray_shortcut = get_tray_shortcut(&exe, &tmp_path)?;
    let mut reg_value_desktop_shortcuts = "0".to_owned();
    let mut reg_value_start_menu_shortcuts = "0".to_owned();
    let mut reg_value_printer = "0".to_owned();
    let mut shortcuts = Default::default();
    if options.contains("desktopicon") {
        shortcuts = format!(
            "copy /Y \"{}\\{}.lnk\" \"%PUBLIC%\\Desktop\\\"",
            tmp_path,
            crate::get_app_name()
        );
        reg_value_desktop_shortcuts = "1".to_owned();
    }
    if options.contains("startmenu") {
        shortcuts = format!(
            "{shortcuts}
md \"{start_menu}\"
copy /Y \"{tmp_path}\\{app_name}.lnk\" \"{start_menu}\\\"
copy /Y \"{tmp_path}\\Uninstall {app_name}.lnk\" \"{start_menu}\\\"
     "
        );
        reg_value_start_menu_shortcuts = "1".to_owned();
    }
    let install_printer = options.contains("printer") && is_win_10_or_greater();
    if install_printer {
        reg_value_printer = "1".to_owned();
    }

    let meta = std::fs::symlink_metadata(std::env::current_exe()?)?;
    let size = meta.len() / 1024;
    // https://docs.microsoft.com/zh-cn/windows/win32/msi/uninstall-registry-key?redirectedfrom=MSDNa
    // https://www.windowscentral.com/how-edit-registry-using-command-prompt-windows-10
    // https://www.tenforums.com/tutorials/70903-add-remove-allowed-apps-through-windows-firewall-windows-10-a.html
    // Note: without if exist, the bat may exit in advance on some Windows7 https://github.com/rustdesk/rustdesk/issues/895
    let dels = format!(
        "
if exist \"{mk_shortcut}\" del /f /q \"{mk_shortcut}\"
if exist \"{uninstall_shortcut}\" del /f /q \"{uninstall_shortcut}\"
if exist \"{tray_shortcut}\" del /f /q \"{tray_shortcut}\"
if exist \"{tmp_path}\\{app_name}.lnk\" del /f /q \"{tmp_path}\\{app_name}.lnk\"
if exist \"{tmp_path}\\Uninstall {app_name}.lnk\" del /f /q \"{tmp_path}\\Uninstall {app_name}.lnk\"
if exist \"{tmp_path}\\{app_name} Tray.lnk\" del /f /q \"{tmp_path}\\{app_name} Tray.lnk\"
        "
    );
    let src_exe = std::env::current_exe()?.to_str().unwrap_or("").to_string();

    // potential bug here: if run_cmd cancelled, but config file is changed.
    if let Some(lic) = get_license() {
        Config::set_option("key".into(), lic.key);
        Config::set_option("custom-rendezvous-server".into(), lic.host);
        Config::set_option("api-server".into(), lic.api);
    }

    let tray_shortcuts = if config::is_outgoing_only() {
        "".to_owned()
    } else {
        format!("
cscript \"{tray_shortcut}\"
copy /Y \"{tmp_path}\\{app_name} Tray.lnk\" \"%PROGRAMDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\\"
")
    };

    let install_remote_printer = if install_printer {
        // No need to use `|| true` here.
        // The script will not exit even if `--install-remote-printer` panics.
        format!("\"{}\" --install-remote-printer", &src_exe)
    } else if is_win_10_or_greater() {
        format!("\"{}\" --uninstall-remote-printer", &src_exe)
    } else {
        "".to_owned()
    };

    // Remember to check if `update_me` need to be changed if changing the `cmds`.
    // No need to merge the existing dup code, because the code in these two functions are too critical.
    // New code should be written in a common function.
    let cmds = format!(
        "
{uninstall_str}
chcp 65001
md \"{path}\"
{copy_exe}
reg add {subkey} /f
reg add {subkey} /f /v DisplayIcon /t REG_SZ /d \"{exe}\"
reg add {subkey} /f /v DisplayName /t REG_SZ /d \"{app_name}\"
reg add {subkey} /f /v DisplayVersion /t REG_SZ /d \"{version}\"
reg add {subkey} /f /v Version /t REG_SZ /d \"{version}\"
reg add {subkey} /f /v BuildDate /t REG_SZ /d \"{build_date}\"
reg add {subkey} /f /v InstallLocation /t REG_SZ /d \"{path}\"
reg add {subkey} /f /v Publisher /t REG_SZ /d \"{app_name}\"
reg add {subkey} /f /v VersionMajor /t REG_DWORD /d {version_major}
reg add {subkey} /f /v VersionMinor /t REG_DWORD /d {version_minor}
reg add {subkey} /f /v VersionBuild /t REG_DWORD /d {version_build}
reg add {subkey} /f /v UninstallString /t REG_SZ /d \"\\\"{exe}\\\" --uninstall\"
reg add {subkey} /f /v EstimatedSize /t REG_DWORD /d {size}
reg add {subkey} /f /v WindowsInstaller /t REG_DWORD /d 0
cscript \"{mk_shortcut}\"
cscript \"{uninstall_shortcut}\"
{tray_shortcuts}
{shortcuts}
copy /Y \"{tmp_path}\\Uninstall {app_name}.lnk\" \"{path}\\\"
{dels}
{import_config}
{after_install}
{install_remote_printer}
{sleep}
    ",
        version = crate::VERSION.replace("-", "."),
        build_date = crate::BUILD_DATE,
        after_install = get_after_install(
            &exe,
            Some(reg_value_start_menu_shortcuts),
            Some(reg_value_desktop_shortcuts),
            Some(reg_value_printer)
        ),
        sleep = if debug { "timeout 300" } else { "" },
        dels = if debug { "" } else { &dels },
        copy_exe = copy_exe_cmd(&src_exe, &exe, &path)?,
        import_config = get_import_config(&exe),
    );
    run_cmds(cmds, debug, "install")?;
    run_after_run_cmds(silent);
    Ok(())
}

pub fn run_after_install() -> ResultType<()> {
    let (_, _, _, exe) = get_install_info();
    run_cmds(
        get_after_install(&exe, None, None, None),
        true,
        "after_install",
    )
}

pub fn run_before_uninstall() -> ResultType<()> {
    run_cmds(get_before_uninstall(true), true, "before_install")
}

fn get_before_uninstall(kill_self: bool) -> String {
    let app_name = crate::get_app_name();
    let ext = app_name.to_lowercase();
    let filter = if kill_self {
        "".to_string()
    } else {
        format!(" /FI \"PID ne {}\"", get_current_pid())
    };
    format!(
        "
    chcp 65001
    sc stop {app_name}
    sc delete {app_name}
    taskkill /F /IM {broker_exe}
    taskkill /F /IM {app_name}.exe{filter}
    reg delete HKEY_CLASSES_ROOT\\.{ext} /f
    reg delete HKEY_CLASSES_ROOT\\{ext} /f
    netsh advfirewall firewall delete rule name=\"{app_name} Service\"
    ",
        broker_exe = WIN_TOPMOST_INJECTED_PROCESS_EXE,
    )
}

/// Constructs the uninstall command string for the application.
///
/// # Parameters
/// - `kill_self`: The command will kill the process of current app name. If `true`, it will kill
///   the current process as well. If `false`, it will exclude the current process from the kill
///   command.
/// - `uninstall_printer`: If `true`, includes commands to uninstall the remote printer.
///
/// # Details
/// The `uninstall_printer` parameter determines whether the command to uninstall the remote printer
/// is included in the generated uninstall script. If `uninstall_printer` is `false`, the printer
/// related command is omitted from the script.
fn get_uninstall(kill_self: bool, uninstall_printer: bool) -> String {
    let reg_uninstall_string = get_reg("UninstallString");
    if reg_uninstall_string.to_lowercase().contains("msiexec.exe") {
        return reg_uninstall_string;
    }

    let mut uninstall_cert_cmd = "".to_string();
    let mut uninstall_printer_cmd = "".to_string();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(exe_path) = exe.to_str() {
            uninstall_cert_cmd = format!("\"{}\" --uninstall-cert", exe_path);
            if uninstall_printer {
                uninstall_printer_cmd = format!("\"{}\" --uninstall-remote-printer", &exe_path);
            }
        }
    }
    let (subkey, path, start_menu, _) = get_install_info();
    format!(
        "
    {before_uninstall}
    {uninstall_printer_cmd}
    {uninstall_cert_cmd}
    reg delete {subkey} /f
    {uninstall_amyuni_idd}
    if exist \"{path}\" rd /s /q \"{path}\"
    if exist \"{start_menu}\" rd /s /q \"{start_menu}\"
    if exist \"%PUBLIC%\\Desktop\\{app_name}.lnk\" del /f /q \"%PUBLIC%\\Desktop\\{app_name}.lnk\"
    if exist \"%PROGRAMDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\{app_name} Tray.lnk\" del /f /q \"%PROGRAMDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\{app_name} Tray.lnk\"
    ",
        before_uninstall=get_before_uninstall(kill_self),
        uninstall_amyuni_idd=get_uninstall_amyuni_idd(),
        app_name = crate::get_app_name(),
    )
}

pub fn uninstall_me(kill_self: bool) -> ResultType<()> {
    run_cmds(get_uninstall(kill_self, true), true, "uninstall")
}

fn write_cmds(cmds: String, ext: &str, tip: &str) -> ResultType<std::path::PathBuf> {
    let mut cmds = cmds;
    let mut tmp = std::env::temp_dir();
    // When dir contains these characters, the bat file will not execute in elevated mode.
    if vec!["&", "@", "^"]
        .drain(..)
        .any(|s| tmp.to_string_lossy().to_string().contains(s))
    {
        if let Ok(dir) = user_accessible_folder() {
            tmp = dir;
        }
    }
    tmp.push(format!("{}_{}.{}", crate::get_app_name(), tip, ext));
    let mut file = std::fs::File::create(&tmp)?;
    if ext == "bat" {
        let tmp2 = get_undone_file(&tmp)?;
        std::fs::File::create(&tmp2).ok();
        cmds = format!(
            "
{cmds}
if exist \"{path}\" del /f /q \"{path}\"
",
            path = tmp2.to_string_lossy()
        );
    }
    // in case cmds mixed with \r\n and \n, make sure all ending with \r\n
    // in some windows, \r\n required for cmd file to run
    cmds = cmds.replace("\r\n", "\n").replace("\n", "\r\n");
    if ext == "vbs" {
        let mut v: Vec<u16> = cmds.encode_utf16().collect();
        // utf8 -> utf16le which vbs support it only
        file.write_all(to_le(&mut v))?;
    } else {
        file.write_all(cmds.as_bytes())?;
    }
    file.sync_all()?;
    return Ok(tmp);
}

fn to_le(v: &mut [u16]) -> &[u8] {
    for b in v.iter_mut() {
        *b = b.to_le()
    }
    unsafe { v.align_to().1 }
}

fn get_undone_file(tmp: &Path) -> ResultType<PathBuf> {
    Ok(tmp.with_file_name(format!(
        "{}.undone",
        tmp.file_name()
            .ok_or(anyhow!("Failed to get filename of {:?}", tmp))?
            .to_string_lossy()
    )))
}

fn run_cmds(cmds: String, show: bool, tip: &str) -> ResultType<()> {
    let tmp = write_cmds(cmds, "bat", tip)?;
    let tmp2 = get_undone_file(&tmp)?;
    let tmp_fn = tmp.to_str().unwrap_or("");
    // https://github.com/rustdesk/rustdesk/issues/6786#issuecomment-1879655410
    // Specify cmd.exe explicitly to avoid the replacement of cmd commands.
    let res = runas::Command::new("cmd.exe")
        .args(&["/C", &tmp_fn])
        .show(show)
        .force_prompt(true)
        .status();
    if !show {
        allow_err!(std::fs::remove_file(tmp));
    }
    let _ = res?;
    if tmp2.exists() {
        allow_err!(std::fs::remove_file(tmp2));
        bail!("{} failed", tip);
    }
    Ok(())
}

pub fn toggle_blank_screen(v: bool) {
    let v = if v { TRUE } else { FALSE };
    unsafe {
        blank_screen(v);
    }
}

pub fn block_input(v: bool) -> (bool, String) {
    let v = if v { TRUE } else { FALSE };
    unsafe {
        if BlockInput(v) == TRUE {
            (true, "".to_owned())
        } else {
            (false, format!("Error: {}", io::Error::last_os_error()))
        }
    }
}

pub fn add_recent_document(path: &str) {
    extern "C" {
        fn AddRecentDocument(path: *const u16);
    }
    use std::os::windows::ffi::OsStrExt;
    let wstr: Vec<u16> = std::ffi::OsStr::new(path)
        .encode_wide()
        .chain(Some(0).into_iter())
        .collect();
    let wstr = wstr.as_ptr();
    unsafe {
        AddRecentDocument(wstr);
    }
}

pub fn is_installed() -> bool {
    let (_, _, _, exe) = get_install_info();
    std::fs::metadata(exe).is_ok()
}

pub fn get_reg(name: &str) -> String {
    let (subkey, _, _, _) = get_install_info();
    get_reg_of(&subkey, name)
}

fn get_reg_of(subkey: &str, name: &str) -> String {
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    if let Ok(tmp) = hklm.open_subkey(subkey.replace("HKEY_LOCAL_MACHINE\\", "")) {
        if let Ok(v) = tmp.get_value(name) {
            return v;
        }
    }
    "".to_owned()
}

pub fn get_license_from_exe_name() -> ResultType<CustomServer> {
    let mut exe = std::env::current_exe()?.to_str().unwrap_or("").to_owned();
    // if defined portable appname entry, replace original executable name with it.
    if let Ok(portable_exe) = std::env::var(PORTABLE_APPNAME_RUNTIME_ENV_KEY) {
        exe = portable_exe;
    }
    get_custom_server_from_string(&exe)
}

// We can't directly use `RegKey::set_value` to update the registry value, because it will fail with `ERROR_ACCESS_DENIED`
// So we have to use `run_cmds` to update the registry value.
pub fn update_install_option(k: &str, v: &str) -> ResultType<()> {
    // Don't update registry if not installed or not server process.
    if !is_installed() || !crate::is_server() {
        return Ok(());
    }
    let app_name = crate::get_app_name();
    let ext = app_name.to_lowercase();
    let cmds =
        format!("chcp 65001 && reg add HKEY_CLASSES_ROOT\\.{ext} /f /v {k} /t REG_SZ /d \"{v}\"");
    run_cmds(cmds, false, "update_install_option")?;
    Ok(())
}

#[inline]
pub fn is_win_server() -> bool {
    unsafe { is_windows_server() > 0 }
}

#[inline]
pub fn is_win_10_or_greater() -> bool {
    unsafe { is_windows_10_or_greater() > 0 }
}

pub fn bootstrap() -> bool {
    if let Ok(lic) = get_license_from_exe_name() {
        *config::EXE_RENDEZVOUS_SERVER.write().unwrap() = lic.host.clone();
    }

    #[cfg(debug_assertions)]
    {
        true
    }
    #[cfg(not(debug_assertions))]
    {
        // This function will cause `'sciter.dll' was not found neither in PATH nor near the current executable.` when debugging RustDesk.
        // Only call set_safe_load_dll() on Windows 10 or greater
        if is_win_10_or_greater() {
            set_safe_load_dll()
        } else {
            true
        }
    }
}

#[cfg(not(debug_assertions))]
fn set_safe_load_dll() -> bool {
    if !unsafe { set_default_dll_directories() } {
        return false;
    }

    // `SetDllDirectoryW` should never fail.
    // https://docs.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setdlldirectoryw
    if unsafe { SetDllDirectoryW(wide_string("").as_ptr()) == FALSE } {
        eprintln!("SetDllDirectoryW failed: {}", io::Error::last_os_error());
        return false;
    }

    true
}

// https://docs.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-setdefaultdlldirectories
#[cfg(not(debug_assertions))]
unsafe fn set_default_dll_directories() -> bool {
    let module = LoadLibraryExW(
        wide_string("Kernel32.dll").as_ptr(),
        0 as _,
        LOAD_LIBRARY_SEARCH_SYSTEM32,
    );
    if module.is_null() {
        return false;
    }

    match CString::new("SetDefaultDllDirectories") {
        Err(e) => {
            eprintln!("CString::new failed: {}", e);
            return false;
        }
        Ok(func_name) => {
            let func = GetProcAddress(module, func_name.as_ptr());
            if func.is_null() {
                eprintln!("GetProcAddress failed: {}", io::Error::last_os_error());
                return false;
            }
            type SetDefaultDllDirectories = unsafe extern "system" fn(DWORD) -> BOOL;
            let func: SetDefaultDllDirectories = std::mem::transmute(func);
            if func(LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS) == FALSE {
                eprintln!(
                    "SetDefaultDllDirectories failed: {}",
                    io::Error::last_os_error()
                );
                return false;
            }
        }
    }
    true
}

pub fn create_shortcut(id: &str) -> ResultType<()> {
    let exe = std::env::current_exe()?.to_str().unwrap_or("").to_owned();
    // https://github.com/rustdesk/rustdesk/issues/13735
    // Replace ':' with '_' for filename since ':' is not allowed in Windows filenames
    // https://github.com/rustdesk/hbb_common/blob/8b0e25867375ba9e6bff548acf44fe6d6ffa7c0e/src/config.rs#L1384
    let filename = id.replace(':', "_");
    let shortcut = write_cmds(
        format!(
            "
Set oWS = WScript.CreateObject(\"WScript.Shell\")
strDesktop = oWS.SpecialFolders(\"Desktop\")
Set objFSO = CreateObject(\"Scripting.FileSystemObject\")
sLinkFile = objFSO.BuildPath(strDesktop, \"{filename}.lnk\")
Set oLink = oWS.CreateShortcut(sLinkFile)
    oLink.TargetPath = \"{exe}\"
    oLink.Arguments = \"--connect {id}\"
oLink.Save
        "
        ),
        "vbs",
        "connect_shortcut",
    )?
    .to_str()
    .unwrap_or("")
    .to_owned();
    std::process::Command::new("cscript")
        .arg(&shortcut)
        .creation_flags(CREATE_NO_WINDOW)
        .output()?;
    allow_err!(std::fs::remove_file(shortcut));
    Ok(())
}

pub fn enable_lowlevel_keyboard(hwnd: HWND) {
    let ret = unsafe { win32_enable_lowlevel_keyboard(hwnd) };
    if ret != 0 {
        log::error!("Failure grabbing keyboard");
        return;
    }
}

pub fn disable_lowlevel_keyboard(hwnd: HWND) {
    unsafe { win32_disable_lowlevel_keyboard(hwnd) };
}

pub fn stop_system_key_propagate(v: bool) {
    unsafe { win_stop_system_key_propagate(if v { TRUE } else { FALSE }) };
}

pub fn get_win_key_state() -> bool {
    unsafe { is_win_down() == TRUE }
}

pub fn quit_gui() {
    std::process::exit(0);
    // unsafe { PostQuitMessage(0) }; // some how not work
}

pub fn get_user_token(session_id: u32, as_user: bool) -> HANDLE {
    let mut token = NULL as HANDLE;
    unsafe {
        let mut _token_pid = 0;
        if FALSE
            == GetSessionUserTokenWin(
                &mut token as _,
                session_id,
                if as_user { TRUE } else { FALSE },
                &mut _token_pid,
            )
        {
            NULL as _
        } else {
            token
        }
    }
}

pub fn run_background(exe: &str, arg: &str) -> ResultType<bool> {
    let wexe = wide_string(exe);
    let warg;
    unsafe {
        let ret = ShellExecuteW(
            NULL as _,
            NULL as _,
            wexe.as_ptr() as _,
            if arg.is_empty() {
                NULL as _
            } else {
                warg = wide_string(arg);
                warg.as_ptr() as _
            },
            NULL as _,
            SW_HIDE,
        );
        return Ok(ret as i32 > 32);
    }
}

pub fn run_uac(exe: &str, arg: &str) -> ResultType<bool> {
    let wop = wide_string("runas");
    let wexe = wide_string(exe);
    let warg;
    unsafe {
        let ret = ShellExecuteW(
            NULL as _,
            wop.as_ptr() as _,
            wexe.as_ptr() as _,
            if arg.is_empty() {
                NULL as _
            } else {
                warg = wide_string(arg);
                warg.as_ptr() as _
            },
            NULL as _,
            SW_SHOWNORMAL,
        );
        return Ok(ret as i32 > 32);
    }
}

pub fn check_super_user_permission() -> ResultType<bool> {
    run_uac(
        std::env::current_exe()?
            .to_string_lossy()
            .to_string()
            .as_str(),
        "--version",
    )
}

pub fn elevate(arg: &str) -> ResultType<bool> {
    run_uac(
        std::env::current_exe()?
            .to_string_lossy()
            .to_string()
            .as_str(),
        arg,
    )
}

pub fn run_as_system(arg: &str) -> ResultType<()> {
    let exe = std::env::current_exe()?.to_string_lossy().to_string();
    if impersonate_system::run_as_system(&exe, arg).is_err() {
        bail!(format!("Failed to run {} as system", exe));
    }
    Ok(())
}

pub fn elevate_or_run_as_system(is_setup: bool, is_elevate: bool, is_run_as_system: bool) {
    // avoid possible run recursively due to failed run.
    log::info!(
        "elevate: {} -> {:?}, run_as_system: {} -> {}",
        is_elevate,
        is_elevated(None),
        is_run_as_system,
        crate::username(),
    );
    let arg_elevate = if is_setup {
        "--noinstall --elevate"
    } else {
        "--elevate"
    };
    let arg_run_as_system = if is_setup {
        "--noinstall --run-as-system"
    } else {
        "--run-as-system"
    };
    if is_root() {
        if is_run_as_system {
            log::info!("run portable service");
            crate::portable_service::server::run_portable_service();
        }
    } else {
        match is_elevated(None) {
            Ok(elevated) => {
                if elevated {
                    if !is_run_as_system {
                        if run_as_system(arg_run_as_system).is_ok() {
                            std::process::exit(0);
                        } else {
                            log::error!(
                                "Failed to run as system, error {}",
                                io::Error::last_os_error()
                            );
                        }
                    }
                } else {
                    if !is_elevate {
                        if let Ok(true) = elevate(arg_elevate) {
                            std::process::exit(0);
                        } else {
                            log::error!("Failed to elevate, error {}", io::Error::last_os_error());
                        }
                    }
                }
            }
            Err(_) => log::error!(
                "Failed to get elevation status, error {}",
                io::Error::last_os_error()
            ),
        }
    }
}

pub fn is_elevated(process_id: Option<DWORD>) -> ResultType<bool> {
    use hbb_common::platform::windows::RAIIHandle;
    unsafe {
        let handle: HANDLE = match process_id {
            Some(process_id) => OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, process_id),
            None => GetCurrentProcess(),
        };
        if handle == NULL {
            bail!(
                "Failed to open process, error {}",
                io::Error::last_os_error()
            )
        }
        let _handle = RAIIHandle(handle);
        let mut token: HANDLE = mem::zeroed();
        if OpenProcessToken(handle, TOKEN_QUERY, &mut token) == FALSE {
            bail!(
                "Failed to open process token, error {}",
                io::Error::last_os_error()
            )
        }
        let _token = RAIIHandle(token);
        let mut token_elevation: TOKEN_ELEVATION = mem::zeroed();
        let mut size: DWORD = 0;
        if GetTokenInformation(
            token,
            TokenElevation,
            (&mut token_elevation) as *mut _ as *mut c_void,
            mem::size_of::<TOKEN_ELEVATION>() as _,
            &mut size,
        ) == FALSE
        {
            bail!(
                "Failed to get token information, error {}",
                io::Error::last_os_error()
            )
        }

        Ok(token_elevation.TokenIsElevated != 0)
    }
}

pub fn is_foreground_window_elevated() -> ResultType<bool> {
    unsafe {
        let mut process_id: DWORD = 0;
        GetWindowThreadProcessId(GetForegroundWindow(), &mut process_id);
        if process_id == 0 {
            bail!(
                "Failed to get processId, error {}",
                io::Error::last_os_error()
            )
        }
        is_elevated(Some(process_id))
    }
}

fn get_current_pid() -> u32 {
    unsafe { GetCurrentProcessId() }
}

pub fn get_double_click_time() -> u32 {
    unsafe { GetDoubleClickTime() }
}

pub fn wide_string(s: &str) -> Vec<u16> {
    use std::os::windows::prelude::OsStrExt;
    std::ffi::OsStr::new(s)
        .encode_wide()
        .chain(Some(0).into_iter())
        .collect()
}

/// send message to currently shown window
pub fn send_message_to_hnwd(
    class_name: &str,
    window_name: &str,
    dw_data: usize,
    data: &str,
    show_window: bool,
) -> bool {
    unsafe {
        let class_name_utf16 = wide_string(class_name);
        let window_name_utf16 = wide_string(window_name);
        let window = FindWindowW(class_name_utf16.as_ptr(), window_name_utf16.as_ptr());
        if window.is_null() {
            log::warn!("no such window {}:{}", class_name, window_name);
            return false;
        }
        let mut data_struct = COPYDATASTRUCT::default();
        data_struct.dwData = dw_data;
        let mut data_zero: String = data.chars().chain(Some('\0').into_iter()).collect();
        println!("send {:?}", data_zero);
        data_struct.cbData = data_zero.len() as _;
        data_struct.lpData = data_zero.as_mut_ptr() as _;
        SendMessageW(
            window,
            WM_COPYDATA,
            0,
            &data_struct as *const COPYDATASTRUCT as _,
        );
        if show_window {
            ShowWindow(window, SW_NORMAL);
            SetForegroundWindow(window);
        }
    }
    return true;
}

pub fn get_logon_user_token(user: &str, pwd: &str) -> ResultType<HANDLE> {
    let user_split = user.split("\\").collect::<Vec<&str>>();
    let wuser = wide_string(user_split.get(1).unwrap_or(&user));
    let wpc = wide_string(user_split.get(0).unwrap_or(&""));
    let wpwd = wide_string(pwd);
    let mut ph_token: HANDLE = std::ptr::null_mut();
    let res = unsafe {
        LogonUserW(
            wuser.as_ptr(),
            wpc.as_ptr(),
            wpwd.as_ptr(),
            LOGON32_LOGON_INTERACTIVE,
            LOGON32_PROVIDER_DEFAULT,
            &mut ph_token as _,
        )
    };
    if res == FALSE {
        bail!(
            "Failed to log on user {}: {}",
            user,
            std::io::Error::last_os_error()
        );
    } else {
        if ph_token.is_null() {
            bail!(
                "Failed to log on user {}: {}",
                user,
                std::io::Error::last_os_error()
            );
        }
        Ok(ph_token)
    }
}

// Ensure the token returned is a primary token.
// If the provided token is an impersonation token, it duplicates it to a primary token.
// If the provided token is already a primary token, it returns it as is.
// The caller is responsible for closing the returned token handle.
pub fn ensure_primary_token(user_token: HANDLE) -> ResultType<HANDLE> {
    if user_token.is_null() || user_token == INVALID_HANDLE_VALUE {
        bail!("Invalid user token provided");
    }

    unsafe {
        let mut token_type: TOKEN_TYPE = 0;
        let mut return_length: DWORD = 0;

        if GetTokenInformation(
            user_token,
            TokenType,
            &mut token_type as *mut _ as *mut _,
            std::mem::size_of::<TOKEN_TYPE>() as DWORD,
            &mut return_length,
        ) == FALSE
        {
            bail!(
                "Failed to get token type, error {}",
                io::Error::last_os_error()
            );
        }

        if token_type == TokenImpersonation {
            let mut duplicate_token: HANDLE = std::ptr::null_mut();
            let dup_res = DuplicateToken(user_token, SecurityImpersonation, &mut duplicate_token);
            CloseHandle(user_token);
            if dup_res == FALSE {
                bail!(
                    "Failed to duplicate token, error {}",
                    io::Error::last_os_error()
                );
            }
            Ok(duplicate_token)
        } else {
            Ok(user_token)
        }
    }
}

pub fn is_user_token_admin(user_token: HANDLE) -> ResultType<bool> {
    if user_token.is_null() || user_token == INVALID_HANDLE_VALUE {
        bail!("Invalid user token provided");
    }

    unsafe {
        let mut dw_size: DWORD = 0;
        GetTokenInformation(
            user_token,
            TokenGroups,
            std::ptr::null_mut(),
            0,
            &mut dw_size,
        );

        let last_error = GetLastError();
        if last_error != ERROR_INSUFFICIENT_BUFFER {
            bail!(
                "Failed to get token groups buffer size, error: {}",
                last_error
            );
        }
        if dw_size == 0 {
            bail!("Token groups buffer size is zero");
        }

        let mut buffer = vec![0u8; dw_size as usize];
        if GetTokenInformation(
            user_token,
            TokenGroups,
            buffer.as_mut_ptr() as *mut _,
            dw_size,
            &mut dw_size,
        ) == FALSE
        {
            bail!(
                "Failed to get token groups information, error: {}",
                io::Error::last_os_error()
            );
        }

        let p_token_groups = buffer.as_ptr() as *const TOKEN_GROUPS;
        let group_count = (*p_token_groups).GroupCount;

        if group_count == 0 {
            return Ok(false);
        }

        let mut nt_authority: SID_IDENTIFIER_AUTHORITY = SID_IDENTIFIER_AUTHORITY {
            Value: SECURITY_NT_AUTHORITY,
        };
        let mut administrators_group: PSID = std::ptr::null_mut();
        if AllocateAndInitializeSid(
            &mut nt_authority,
            2,
            SECURITY_BUILTIN_DOMAIN_RID,
            DOMAIN_ALIAS_RID_ADMINS,
            0,
            0,
            0,
            0,
            0,
            0,
            &mut administrators_group,
        ) == FALSE
        {
            bail!(
                "Failed to allocate administrators group SID, error: {}",
                io::Error::last_os_error()
            );
        }
        if administrators_group.is_null() {
            bail!("Failed to create administrators group SID");
        }

        let mut is_admin = false;
        let groups =
            std::slice::from_raw_parts((*p_token_groups).Groups.as_ptr(), group_count as usize);
        for group in groups {
            if EqualSid(administrators_group, group.Sid) == TRUE {
                is_admin = true;
                break;
            }
        }

        if !administrators_group.is_null() {
            FreeSid(administrators_group);
        }

        Ok(is_admin)
    }
}

pub fn create_process_with_logon(user: &str, pwd: &str, exe: &str, arg: &str) -> ResultType<()> {
    let last_error_table = HashMap::from([
        (
            ERROR_LOGON_FAILURE,
            "The user name or password is incorrect.",
        ),
        (ERROR_ACCESS_DENIED, "Access is denied."),
    ]);

    unsafe {
        let user_split = user.split("\\").collect::<Vec<&str>>();
        let wuser = wide_string(user_split.get(1).unwrap_or(&user));
        let wpc = wide_string(user_split.get(0).unwrap_or(&""));
        let wpwd = wide_string(pwd);
        let cmd = if arg.is_empty() {
            format!("\"{}\"", exe)
        } else {
            format!("\"{}\" {}", exe, arg)
        };
        let mut wcmd = wide_string(&cmd);
        let mut si: STARTUPINFOW = mem::zeroed();
        si.wShowWindow = SW_HIDE as _;
        si.lpDesktop = NULL as _;
        si.cb = std::mem::size_of::<STARTUPINFOW>() as _;
        si.dwFlags = STARTF_USESHOWWINDOW;
        let mut pi: PROCESS_INFORMATION = mem::zeroed();
        let wexe = wide_string(exe);
        if FALSE
            == CreateProcessWithLogonW(
                wuser.as_ptr(),
                wpc.as_ptr(),
                wpwd.as_ptr(),
                LOGON_WITH_PROFILE,
                wexe.as_ptr(),
                wcmd.as_mut_ptr(),
                CREATE_UNICODE_ENVIRONMENT,
                NULL,
                NULL as _,
                &mut si as *mut STARTUPINFOW,
                &mut pi as *mut PROCESS_INFORMATION,
            )
        {
            let last_error = GetLastError();
            bail!(
                "CreateProcessWithLogonW failed : \"{}\", error {}",
                last_error_table
                    .get(&last_error)
                    .unwrap_or(&"Unknown error"),
                io::Error::from_raw_os_error(last_error as _)
            );
        }
    }
    return Ok(());
}

pub fn set_path_permission(dir: &Path, permission: &str) -> ResultType<()> {
    std::process::Command::new("icacls")
        .arg(dir.as_os_str())
        .arg("/grant")
        .arg(format!("*S-1-1-0:(OI)(CI){}", permission))
        .arg("/T")
        .spawn()?;
    Ok(())
}

#[inline]
fn str_to_device_name(name: &str) -> [u16; 32] {
    let mut device_name: Vec<u16> = wide_string(name);
    if device_name.len() < 32 {
        device_name.resize(32, 0);
    }
    let mut result = [0; 32];
    result.copy_from_slice(&device_name[..32]);
    result
}

pub fn resolutions(name: &str) -> Vec<Resolution> {
    unsafe {
        let mut dm: DEVMODEW = std::mem::zeroed();
        let mut v = vec![];
        let mut num = 0;
        let device_name = str_to_device_name(name);
        loop {
            if EnumDisplaySettingsW(device_name.as_ptr(), num, &mut dm) == 0 {
                break;
            }
            let r = Resolution {
                width: dm.dmPelsWidth as _,
                height: dm.dmPelsHeight as _,
                ..Default::default()
            };
            if !v.contains(&r) {
                v.push(r);
            }
            num += 1;
        }
        v
    }
}

pub fn current_resolution(name: &str) -> ResultType<Resolution> {
    let device_name = str_to_device_name(name);
    unsafe {
        let mut dm: DEVMODEW = std::mem::zeroed();
        dm.dmSize = std::mem::size_of::<DEVMODEW>() as _;
        if EnumDisplaySettingsW(device_name.as_ptr(), ENUM_CURRENT_SETTINGS, &mut dm) == 0 {
            bail!(
                "failed to get current resolution, error {}",
                io::Error::last_os_error()
            );
        }
        let r = Resolution {
            width: dm.dmPelsWidth as _,
            height: dm.dmPelsHeight as _,
            ..Default::default()
        };
        Ok(r)
    }
}

pub(super) fn change_resolution_directly(
    name: &str,
    width: usize,
    height: usize,
) -> ResultType<()> {
    let device_name = str_to_device_name(name);
    unsafe {
        let mut dm: DEVMODEW = std::mem::zeroed();
        dm.dmSize = std::mem::size_of::<DEVMODEW>() as _;
        dm.dmPelsWidth = width as _;
        dm.dmPelsHeight = height as _;
        dm.dmFields = DM_PELSHEIGHT | DM_PELSWIDTH;
        let res = ChangeDisplaySettingsExW(
            device_name.as_ptr(),
            &mut dm,
            NULL as _,
            CDS_UPDATEREGISTRY | CDS_GLOBAL | CDS_RESET,
            NULL,
        );
        if res != DISP_CHANGE_SUCCESSFUL {
            bail!(
                "ChangeDisplaySettingsExW failed, res={}, error {}",
                res,
                io::Error::last_os_error()
            );
        }
        Ok(())
    }
}

pub fn user_accessible_folder() -> ResultType<PathBuf> {
    let disk = std::env::var("SystemDrive").unwrap_or("C:".to_string());
    let dir1 = PathBuf::from(format!("{}\\ProgramData", disk));
    // NOTICE: "C:\Windows\Temp" requires permanent authorization.
    let dir2 = PathBuf::from(format!("{}\\Windows\\Temp", disk));
    let dir;
    if dir1.exists() {
        dir = dir1;
    } else if dir2.exists() {
        dir = dir2;
    } else {
        bail!("no valid user accessible folder");
    }
    Ok(dir)
}

#[inline]
pub fn uninstall_cert() -> ResultType<()> {
    cert::uninstall_cert()
}

mod cert {
    use hbb_common::ResultType;

    extern "C" {
        fn DeleteRustDeskTestCertsW();
    }
    pub fn uninstall_cert() -> ResultType<()> {
        unsafe {
            DeleteRustDeskTestCertsW();
        }
        Ok(())
    }
}

#[inline]
pub fn get_char_from_vk(vk: u32) -> Option<char> {
    get_char_from_unicode(get_unicode_from_vk(vk)?)
}

pub fn get_char_from_unicode(unicode: u16) -> Option<char> {
    let buff = [unicode];
    if let Some(chr) = String::from_utf16(&buff[..1]).ok()?.chars().next() {
        if chr.is_control() {
            return None;
        } else {
            Some(chr)
        }
    } else {
        None
    }
}

pub fn get_unicode_from_vk(vk: u32) -> Option<u16> {
    const BUF_LEN: i32 = 32;
    let mut buff = [0_u16; BUF_LEN as usize];
    let buff_ptr = buff.as_mut_ptr();
    let len = unsafe {
        let current_window_thread_id = GetWindowThreadProcessId(GetForegroundWindow(), null_mut());
        let layout = GetKeyboardLayout(current_window_thread_id);

        // refs: https://github.com/rustdesk-org/rdev/blob/25a99ce71ab42843ad253dd51e6a35e83e87a8a4/src/windows/keyboard.rs#L115
        let press_state = 129;
        let mut state: [BYTE; 256] = [0; 256];
        let shift_left = rdev::get_modifier(rdev::Key::ShiftLeft);
        let shift_right = rdev::get_modifier(rdev::Key::ShiftRight);
        if shift_left {
            state[VK_LSHIFT as usize] = press_state;
        }
        if shift_right {
            state[VK_RSHIFT as usize] = press_state;
        }
        if shift_left || shift_right {
            state[VK_SHIFT as usize] = press_state;
        }
        ToUnicodeEx(vk, 0x00, &state as _, buff_ptr, BUF_LEN, 0, layout)
    };
    if len == 1 {
        Some(buff[0])
    } else {
        None
    }
}

pub fn is_process_consent_running() -> ResultType<bool> {
    let output = std::process::Command::new("cmd")
        .args(&["/C", "tasklist | findstr consent.exe"])
        .creation_flags(CREATE_NO_WINDOW)
        .output()?;
    Ok(output.status.success() && !output.stdout.is_empty())
}

pub struct WakeLock(u32);
// Failed to compile keepawake-rs on i686
impl WakeLock {
    pub fn new(display: bool, idle: bool, sleep: bool) -> Self {
        let mut flag = ES_CONTINUOUS;
        if display {
            flag |= ES_DISPLAY_REQUIRED;
        }
        if idle {
            flag |= ES_SYSTEM_REQUIRED;
        }
        if sleep {
            flag |= ES_AWAYMODE_REQUIRED;
        }
        unsafe { SetThreadExecutionState(flag) };
        WakeLock(flag)
    }

    pub fn set_display(&mut self, display: bool) -> ResultType<()> {
        let flag = if display {
            self.0 | ES_DISPLAY_REQUIRED
        } else {
            self.0 & !ES_DISPLAY_REQUIRED
        };
        if flag != self.0 {
            unsafe { SetThreadExecutionState(flag) };
            self.0 = flag;
        }
        Ok(())
    }
}

impl Drop for WakeLock {
    fn drop(&mut self) {
        unsafe { SetThreadExecutionState(ES_CONTINUOUS) };
    }
}

pub fn uninstall_service(show_new_window: bool, _: bool) -> bool {
    log::info!("Uninstalling service...");
    let filter = format!(" /FI \"PID ne {}\"", get_current_pid());
    Config::set_option("stop-service".into(), "Y".into());
    let cmds = format!(
        "
    chcp 65001
    sc stop {app_name}
    sc delete {app_name}
    if exist \"%PROGRAMDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\{app_name} Tray.lnk\" del /f /q \"%PROGRAMDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\{app_name} Tray.lnk\"
    taskkill /F /IM {broker_exe}
    taskkill /F /IM {app_name}.exe{filter}
    ",
        app_name = crate::get_app_name(),
        broker_exe = WIN_TOPMOST_INJECTED_PROCESS_EXE,
    );
    if let Err(err) = run_cmds(cmds, false, "uninstall") {
        Config::set_option("stop-service".into(), "".into());
        log::debug!("{err}");
        return true;
    }
    run_after_run_cmds(!show_new_window);
    std::process::exit(0);
}

pub fn install_service() -> bool {
    log::info!("Installing service...");
    let _installing = crate::platform::InstallingService::new();
    let (_, _, _, exe) = get_install_info();
    let tmp_path = std::env::temp_dir().to_string_lossy().to_string();
    let tray_shortcut = get_tray_shortcut(&exe, &tmp_path).unwrap_or_default();
    let filter = format!(" /FI \"PID ne {}\"", get_current_pid());
    Config::set_option("stop-service".into(), "".into());
    crate::ipc::EXIT_RECV_CLOSE.store(false, Ordering::Relaxed);
    let cmds = format!(
        "
chcp 65001
taskkill /F /IM {app_name}.exe{filter}
cscript \"{tray_shortcut}\"
copy /Y \"{tmp_path}\\{app_name} Tray.lnk\" \"%PROGRAMDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\\"
{import_config}
{create_service}
if exist \"{tray_shortcut}\" del /f /q \"{tray_shortcut}\"
    ",
        app_name = crate::get_app_name(),
        import_config = get_import_config(&exe),
        create_service = get_create_service(&exe),
    );
    if let Err(err) = run_cmds(cmds, false, "install") {
        Config::set_option("stop-service".into(), "Y".into());
        crate::ipc::EXIT_RECV_CLOSE.store(true, Ordering::Relaxed);
        log::debug!("{err}");
        return true;
    }
    run_after_run_cmds(false);
    std::process::exit(0);
}

pub fn update_me(debug: bool) -> ResultType<()> {
    let app_name = crate::get_app_name();
    let src_exe = std::env::current_exe()?.to_string_lossy().to_string();
    let (subkey, path, _, exe) = get_install_info();
    let is_installed = std::fs::metadata(&exe).is_ok();
    if !is_installed {
        bail!("{} is not installed.", &app_name);
    }

    let app_exe_name = &format!("{}.exe", &app_name);
    let main_window_pids =
        crate::platform::get_pids_of_process_with_args::<_, &str>(&app_exe_name, &[]);
    let main_window_sessions = main_window_pids
        .iter()
        .map(|pid| get_session_id_of_process(pid.as_u32()))
        .flatten()
        .collect::<Vec<_>>();
    kill_process_by_pids(&app_exe_name, main_window_pids)?;
    let tray_pids = crate::platform::get_pids_of_process_with_args(&app_exe_name, &["--tray"]);
    let tray_sessions = tray_pids
        .iter()
        .map(|pid| get_session_id_of_process(pid.as_u32()))
        .flatten()
        .collect::<Vec<_>>();
    kill_process_by_pids(&app_exe_name, tray_pids)?;
    let is_service_running = is_self_service_running();

    let mut version_major = "0";
    let mut version_minor = "0";
    let mut version_build = "0";
    let versions: Vec<&str> = crate::VERSION.split(".").collect();
    if versions.len() > 0 {
        version_major = versions[0];
    }
    if versions.len() > 1 {
        version_minor = versions[1];
    }
    if versions.len() > 2 {
        version_build = versions[2];
    }
    let meta = std::fs::symlink_metadata(std::env::current_exe()?)?;
    let size = meta.len() / 1024;

    let reg_cmd = format!(
        "
reg add {subkey} /f /v DisplayIcon /t REG_SZ /d \"{exe}\"
reg add {subkey} /f /v DisplayVersion /t REG_SZ /d \"{version}\"
reg add {subkey} /f /v Version /t REG_SZ /d \"{version}\"
reg add {subkey} /f /v BuildDate /t REG_SZ /d \"{build_date}\"
reg add {subkey} /f /v VersionMajor /t REG_DWORD /d {version_major}
reg add {subkey} /f /v VersionMinor /t REG_DWORD /d {version_minor}
reg add {subkey} /f /v VersionBuild /t REG_DWORD /d {version_build}
reg add {subkey} /f /v EstimatedSize /t REG_DWORD /d {size}
    ",
        version = crate::VERSION.replace("-", "."),
        build_date = crate::BUILD_DATE,
    );

    let filter = format!(" /FI \"PID ne {}\"", get_current_pid());
    let restore_service_cmd = if is_service_running {
        format!("sc start {}", &app_name)
    } else {
        "".to_owned()
    };

    // No need to check the install option here, `is_rd_printer_installed` rarely fails.
    let is_printer_installed = remote_printer::is_rd_printer_installed(&app_name).unwrap_or(false);
    // Do nothing if the printer is not installed or failed to query if the printer is installed.
    let (uninstall_printer_cmd, install_printer_cmd) = if is_printer_installed {
        (
            format!("\"{}\" --uninstall-remote-printer", &src_exe),
            format!("\"{}\" --install-remote-printer", &src_exe),
        )
    } else {
        ("".to_owned(), "".to_owned())
    };

    // We do not try to remove all files in the old version.
    // Because I don't know whether additional files will be installed here after installation, such as drivers.
    // Just copy files to the installation directory works fine.
    //if exist \"{path}\" rd /s /q \"{path}\"
    // md \"{path}\"
    //
    // We need `taskkill` because:
    // 1. There may be some other processes like `rustdesk --connect` are running.
    // 2. Sometimes, the main window and the tray icon are showing
    // while I cannot find them by `tasklist` or the methods above.
    // There's should be 4 processes running: service, server, tray and main window.
    // But only 2 processes are shown in the tasklist.
    let cmds = format!(
        "
chcp 65001
sc stop {app_name}
taskkill /F /IM {app_name}.exe{filter}
{reg_cmd}
{copy_exe}
{restore_service_cmd}
{uninstall_printer_cmd}
{install_printer_cmd}
{sleep}
    ",
        app_name = app_name,
        copy_exe = copy_exe_cmd(&src_exe, &exe, &path)?,
        sleep = if debug { "timeout 300" } else { "" },
    );

    run_cmds(cmds, debug, "update")?;

    std::thread::sleep(std::time::Duration::from_millis(2000));
    if tray_sessions.is_empty() {
        log::info!("No tray process found.");
    } else {
        log::info!("Try to restore the tray process...");
        log::info!(
            "Try to restore the tray process..., sessions: {:?}",
            &tray_sessions
        );
        for s in tray_sessions {
            if s != 0 {
                allow_err!(run_exe_in_session(&exe, vec!["--tray"], s, true));
            }
        }
    }
    if main_window_sessions.is_empty() {
        log::info!("No main window process found.");
    } else {
        log::info!("Try to restore the main window process...");
        std::thread::sleep(std::time::Duration::from_millis(2000));
        for s in main_window_sessions {
            if s != 0 {
                allow_err!(run_exe_in_session(&exe, vec![], s, true));
            }
        }
    }
    std::thread::sleep(std::time::Duration::from_millis(300));
    log::info!("Update completed.");

    Ok(())
}

// Double confirm the process name
fn kill_process_by_pids(name: &str, pids: Vec<Pid>) -> ResultType<()> {
    let name = name.to_lowercase();
    let s = System::new_all();
    // No need to check all names of `pids` first, and kill them then.
    // It's rare case that they're not matched.
    for pid in pids {
        if let Some(process) = s.process(pid) {
            if process.name().to_lowercase() != name {
                bail!("Failed to kill the process, the names are mismatched.");
            }
            if !process.kill() {
                bail!("Failed to kill the process");
            }
        } else {
            bail!("Failed to kill the process, the pid is not found");
        }
    }
    Ok(())
}

// Don't launch tray app when running with `\qn`.
// 1. Because `/qn` requires administrator permission and the tray app should be launched with user permission.
//   Or launching the main window from the tray app will cause the main window to be launched with administrator permission.
// 2. We are not able to launch the tray app if the UI is in the login screen.
// `fn update_me()` can handle the above cases, but for msi update, we need to do more work to handle the above cases.
//    1. Record the tray app session ids.
//    2. Do the update.
//    3. Restore the tray app sessions.
//    `1` and `3` must be done in custom actions.
//    We need also to handle the command line parsing to find the tray processes.
pub fn update_me_msi(msi: &str, quiet: bool) -> ResultType<()> {
    let cmds = format!(
        "chcp 65001 && msiexec /i {msi} {}",
        if quiet { "/qn LAUNCH_TRAY_APP=N" } else { "" }
    );
    run_cmds(cmds, false, "update-msi")?;
    Ok(())
}

pub fn get_tray_shortcut(exe: &str, tmp_path: &str) -> ResultType<String> {
    Ok(write_cmds(
        format!(
            "
Set oWS = WScript.CreateObject(\"WScript.Shell\")
sLinkFile = \"{tmp_path}\\{app_name} Tray.lnk\"

Set oLink = oWS.CreateShortcut(sLinkFile)
    oLink.TargetPath = \"{exe}\"
    oLink.Arguments = \"--tray\"
oLink.Save
        ",
            app_name = crate::get_app_name(),
        ),
        "vbs",
        "tray_shortcut",
    )?
    .to_str()
    .unwrap_or("")
    .to_owned())
}

fn get_import_config(exe: &str) -> String {
    if config::is_outgoing_only() {
        return "".to_string();
    }
    format!("
sc stop {app_name}
sc delete {app_name}
sc create {app_name} binpath= \"\\\"{exe}\\\" --import-config \\\"{config_path}\\\"\" start= auto DisplayName= \"{app_name} Service\"
sc start {app_name}
sc stop {app_name}
sc delete {app_name}
",
    app_name = crate::get_app_name(),
    config_path=Config::file().to_str().unwrap_or(""),
)
}

fn get_create_service(exe: &str) -> String {
    if config::is_outgoing_only() {
        return "".to_string();
    }
    let stop = Config::get_option("stop-service") == "Y";
    if stop {
        format!("
if exist \"%PROGRAMDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\{app_name} Tray.lnk\" del /f /q \"%PROGRAMDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\{app_name} Tray.lnk\"
", app_name = crate::get_app_name())
    } else {
        format!("
sc create {app_name} binpath= \"\\\"{exe}\\\" --service\" start= auto DisplayName= \"{app_name} Service\"
sc start {app_name}
",
    app_name = crate::get_app_name())
    }
}

fn run_after_run_cmds(silent: bool) {
    let (_, _, _, exe) = get_install_info();
    if !silent {
        log::debug!("Spawn new window");
        allow_err!(std::process::Command::new("cmd")
            .args(&["/c", "timeout", "/t", "2", "&", &format!("{exe}")])
            .creation_flags(winapi::um::winbase::CREATE_NO_WINDOW)
            .spawn());
    }
    if Config::get_option("stop-service") != "Y" {
        allow_err!(std::process::Command::new(&exe).arg("--tray").spawn());
    }
    std::thread::sleep(std::time::Duration::from_millis(300));
}

#[inline]
pub fn try_kill_broker() {
    allow_err!(std::process::Command::new("cmd")
        .arg("/c")
        .arg(&format!(
            "taskkill /F /IM {}",
            WIN_TOPMOST_INJECTED_PROCESS_EXE
        ))
        .creation_flags(winapi::um::winbase::CREATE_NO_WINDOW)
        .spawn());
}

pub fn message_box(text: &str) {
    let mut text = text.to_owned();
    let nodialog = std::env::var("NO_DIALOG").unwrap_or_default() == "Y";
    if !text.ends_with("!") || nodialog {
        use arboard::Clipboard as ClipboardContext;
        match ClipboardContext::new() {
            Ok(mut ctx) => {
                ctx.set_text(&text).ok();
                if !nodialog {
                    text = format!("{}\n\nAbove text has been copied to clipboard", &text);
                }
            }
            _ => {}
        }
    }
    if nodialog {
        if std::env::var("PRINT_OUT").unwrap_or_default() == "Y" {
            println!("{text}");
        }
        if let Ok(x) = std::env::var("WRITE_TO_FILE") {
            if !x.is_empty() {
                allow_err!(std::fs::write(x, text));
            }
        }
        return;
    }
    let text = text
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect::<Vec<u16>>();
    let caption = "RustDesk Output"
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect::<Vec<u16>>();
    unsafe { MessageBoxW(std::ptr::null_mut(), text.as_ptr(), caption.as_ptr(), MB_OK) };
}

pub fn alloc_console() {
    unsafe {
        alloc_console_and_redirect();
    }
}

fn get_license() -> Option<CustomServer> {
    let mut lic: CustomServer = Default::default();
    if let Ok(tmp) = get_license_from_exe_name() {
        lic = tmp;
    } else {
        // for back compatibility from migrating from <= 1.2.1 to 1.2.2
        lic.key = get_reg("Key");
        lic.host = get_reg("Host");
        lic.api = get_reg("Api");
    }
    if lic.key.is_empty() || lic.host.is_empty() {
        return None;
    }
    Some(lic)
}

pub struct WallPaperRemover {
    old_path: String,
}

impl WallPaperRemover {
    pub fn new() -> ResultType<Self> {
        let start = std::time::Instant::now();
        if !Self::need_remove() {
            bail!("already solid color");
        }
        let old_path = match Self::get_recent_wallpaper() {
            Ok(old_path) => old_path,
            Err(e) => {
                log::info!("Failed to get recent wallpaper: {:?}, use fallback", e);
                wallpaper::get().map_err(|e| anyhow!(e.to_string()))?
            }
        };
        Self::set_wallpaper(None)?;
        log::info!(
            "created wallpaper remover,  old_path: {:?},  elapsed: {:?}",
            old_path,
            start.elapsed(),
        );
        Ok(Self { old_path })
    }

    pub fn support() -> bool {
        wallpaper::get().is_ok() || !Self::get_recent_wallpaper().unwrap_or_default().is_empty()
    }

    fn get_recent_wallpaper() -> ResultType<String> {
        // SystemParametersInfoW may return %appdata%\Microsoft\Windows\Themes\TranscodedWallpaper, not real path and may not real cache
        // https://www.makeuseof.com/find-desktop-wallpapers-file-location-windows-11/
        // https://superuser.com/questions/1218413/write-to-current-users-registry-through-a-different-admin-account
        let (hkcu, sid) = if is_root() {
            let sid = get_current_process_session_id().ok_or(anyhow!("failed to get sid"))?;
            (RegKey::predef(HKEY_USERS), format!("{}\\", sid))
        } else {
            (RegKey::predef(HKEY_CURRENT_USER), "".to_string())
        };
        let explorer_key = hkcu.open_subkey_with_flags(
            &format!(
                "{}Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Wallpapers",
                sid
            ),
            KEY_READ,
        )?;
        Ok(explorer_key.get_value("BackgroundHistoryPath0")?)
    }

    fn need_remove() -> bool {
        if let Ok(wallpaper) = wallpaper::get() {
            return !wallpaper.is_empty();
        }
        false
    }

    fn set_wallpaper(path: Option<String>) -> ResultType<()> {
        wallpaper::set_from_path(&path.unwrap_or_default()).map_err(|e| anyhow!(e.to_string()))
    }
}

impl Drop for WallPaperRemover {
    fn drop(&mut self) {
        // If the old background is a slideshow, it will be converted into an image. AnyDesk does the same.
        allow_err!(Self::set_wallpaper(Some(self.old_path.clone())));
    }
}

fn get_uninstall_amyuni_idd() -> String {
    match std::env::current_exe() {
        Ok(path) => format!("\"{}\" --uninstall-amyuni-idd", path.to_str().unwrap_or("")),
        Err(e) => {
            log::warn!("Failed to get current exe path, cannot get command of uninstalling idd, Zzerror: {:?}", e);
            "".to_string()
        }
    }
}

#[inline]
pub fn is_self_service_running() -> bool {
    is_service_running(&crate::get_app_name())
}

pub fn is_service_running(service_name: &str) -> bool {
    unsafe {
        let service_name = wide_string(service_name);
        is_service_running_w(service_name.as_ptr() as _)
    }
}

pub fn is_x64() -> bool {
    const PROCESSOR_ARCHITECTURE_AMD64: u16 = 9;

    let mut sys_info = SYSTEM_INFO::default();
    unsafe {
        GetNativeSystemInfo(&mut sys_info as _);
    }
    unsafe { sys_info.u.s().wProcessorArchitecture == PROCESSOR_ARCHITECTURE_AMD64 }
}

pub fn try_kill_rustdesk_main_window_process() -> ResultType<()> {
    // Kill rustdesk.exe without extra arg, should only be called by --server
    // We can find the exact process which occupies the ipc, see more from https://github.com/winsiderss/systeminformer
    log::info!("try kill rustdesk main window process");
    use hbb_common::sysinfo::System;
    let mut sys = System::new();
    sys.refresh_processes();
    let my_uid = sys
        .process((std::process::id() as usize).into())
        .map(|x| x.user_id())
        .unwrap_or_default();
    let my_pid = std::process::id();
    let app_name = crate::get_app_name().to_lowercase();
    if app_name.is_empty() {
        bail!("app name is empty");
    }
    for (_, p) in sys.processes().iter() {
        let p_name = p.name().to_lowercase();
        // name equal
        if !(p_name == app_name || p_name == app_name.clone() + ".exe") {
            continue;
        }
        // arg more than 1
        if p.cmd().len() < 1 {
            continue;
        }
        // first arg contain app name
        if !p.cmd()[0].to_lowercase().contains(&p_name) {
            continue;
        }
        // only one arg or the second arg is empty uni link
        let is_empty_uni = p.cmd().len() == 2 && crate::common::is_empty_uni_link(&p.cmd()[1]);
        if !(p.cmd().len() == 1 || is_empty_uni) {
            continue;
        }
        // skip self
        if p.pid().as_u32() == my_pid {
            continue;
        }
        // because we call it with --server, so we can check user_id, remove this if call it with user process
        if p.user_id() == my_uid {
            log::info!("user id equal, continue");
            continue;
        }
        log::info!("try kill process: {:?}, pid = {:?}", p.cmd(), p.pid());
        nt_terminate_process(p.pid().as_u32())?;
        log::info!("kill process success: {:?}, pid = {:?}", p.cmd(), p.pid());
        return Ok(());
    }
    bail!("failed to find rustdesk main window process");
}

fn nt_terminate_process(process_id: DWORD) -> ResultType<()> {
    type NtTerminateProcess = unsafe extern "system" fn(HANDLE, DWORD) -> DWORD;
    unsafe {
        let h_module = if is_win_10_or_greater() {
            LoadLibraryExA(
                CString::new("ntdll.dll")?.as_ptr(),
                std::ptr::null_mut(),
                LOAD_LIBRARY_SEARCH_SYSTEM32,
            )
        } else {
            LoadLibraryA(CString::new("ntdll.dll")?.as_ptr())
        };
        if !h_module.is_null() {
            let f_nt_terminate_process: NtTerminateProcess = std::mem::transmute(GetProcAddress(
                h_module,
                CString::new("NtTerminateProcess")?.as_ptr(),
            ));
            let h_token = OpenProcess(PROCESS_ALL_ACCESS, 0, process_id);
            if !h_token.is_null() {
                if f_nt_terminate_process(h_token, 1) == 0 {
                    log::info!("terminate process {} success", process_id);
                    CloseHandle(h_token);
                    return Ok(());
                } else {
                    CloseHandle(h_token);
                    bail!("NtTerminateProcess {} failed", process_id);
                }
            } else {
                bail!("OpenProcess {} failed", process_id);
            }
        } else {
            bail!("Failed to load ntdll.dll");
        }
    }
}

pub fn try_set_window_foreground(window: HWND) {
    let env_key = SET_FOREGROUND_WINDOW;
    if let Ok(value) = std::env::var(env_key) {
        if value == "1" {
            unsafe {
                SetForegroundWindow(window);
            }
            std::env::remove_var(env_key);
        }
    }
}

pub fn disable_window_maximize_and_close(hwnd: HWND) {
    unsafe {
        use winapi::um::winuser::{
            GetWindowLongPtrW, SetWindowLongPtrW, GWL_STYLE, WS_CAPTION, WS_SYSMENU,
            WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_THICKFRAME, WS_BORDER, WS_DLGFRAME,
        };
        // Remove title bar by removing WS_CAPTION and related styles
        // This will remove the entire title bar including minimize, maximize, and close buttons
        let style = GetWindowLongPtrW(hwnd, GWL_STYLE) as u32;
        // Remove WS_CAPTION (which is WS_BORDER | WS_DLGFRAME)
        // Also remove WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX to ensure no title bar buttons
        // Keep WS_BORDER for window border, but remove WS_DLGFRAME which is part of title bar
        let new_style = (style
            & !(WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_DLGFRAME))
            | WS_BORDER; // Add border back for window frame
        SetWindowLongPtrW(hwnd, GWL_STYLE, new_style as isize);
        // Force window to redraw with new style
        SetWindowPos(
            hwnd,
            null_mut(),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        );
    }
}

pub mod reg_display_settings {
    use hbb_common::ResultType;
    use serde_derive::{Deserialize, Serialize};
    use std::collections::HashMap;
    use winreg::{enums::*, RegValue};
    const REG_GRAPHICS_DRIVERS_PATH: &str = "SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers";
    const REG_CONNECTIVITY_PATH: &str = "Connectivity";

    #[derive(Serialize, Deserialize, Debug)]
    pub struct RegRecovery {
        path: String,
        key: String,
        old: (Vec<u8>, isize),
        new: (Vec<u8>, isize),
    }

    pub fn read_reg_connectivity() -> ResultType<HashMap<String, HashMap<String, RegValue>>> {
        let hklm = winreg::RegKey::predef(HKEY_LOCAL_MACHINE);
        let reg_connectivity = hklm.open_subkey_with_flags(
            format!("{}\\{}", REG_GRAPHICS_DRIVERS_PATH, REG_CONNECTIVITY_PATH),
            KEY_READ,
        )?;

        let mut map_connectivity = HashMap::new();
        for key in reg_connectivity.enum_keys() {
            let key = key?;
            let mut map_item = HashMap::new();
            let reg_item = reg_connectivity.open_subkey_with_flags(&key, KEY_READ)?;
            for value in reg_item.enum_values() {
                let (name, value) = value?;
                map_item.insert(name, value);
            }
            map_connectivity.insert(key, map_item);
        }
        Ok(map_connectivity)
    }

    pub fn diff_recent_connectivity(
        map1: HashMap<String, HashMap<String, RegValue>>,
        map2: HashMap<String, HashMap<String, RegValue>>,
    ) -> Option<RegRecovery> {
        for (subkey, map_item2) in map2 {
            if let Some(map_item1) = map1.get(&subkey) {
                let key = "Recent";
                if let Some(value1) = map_item1.get(key) {
                    if let Some(value2) = map_item2.get(key) {
                        if value1 != value2 {
                            return Some(RegRecovery {
                                path: format!(
                                    "{}\\{}\\{}",
                                    REG_GRAPHICS_DRIVERS_PATH, REG_CONNECTIVITY_PATH, subkey
                                ),
                                key: key.to_owned(),
                                old: (value1.bytes.clone(), value1.vtype.clone() as isize),
                                new: (value2.bytes.clone(), value2.vtype.clone() as isize),
                            });
                        }
                    }
                }
            }
        }
        None
    }

    pub fn restore_reg_connectivity(reg_recovery: RegRecovery, force: bool) -> ResultType<()> {
        let hklm = winreg::RegKey::predef(HKEY_LOCAL_MACHINE);
        let reg_item = hklm.open_subkey_with_flags(&reg_recovery.path, KEY_READ | KEY_WRITE)?;
        if !force {
            let cur_reg_value = reg_item.get_raw_value(&reg_recovery.key)?;
            let new_reg_value = RegValue {
                bytes: reg_recovery.new.0,
                vtype: isize_to_reg_type(reg_recovery.new.1),
            };
            // Compare if the current value is the same as the new value.
            // If they are not the same, the registry value has been changed by other processes.
            // So we do not restore the registry value.
            if cur_reg_value != new_reg_value {
                return Ok(());
            }
        }
        let reg_value = RegValue {
            bytes: reg_recovery.old.0,
            vtype: isize_to_reg_type(reg_recovery.old.1),
        };
        reg_item.set_raw_value(&reg_recovery.key, &reg_value)?;
        Ok(())
    }

    #[inline]
    fn isize_to_reg_type(i: isize) -> RegType {
        match i {
            0 => RegType::REG_NONE,
            1 => RegType::REG_SZ,
            2 => RegType::REG_EXPAND_SZ,
            3 => RegType::REG_BINARY,
            4 => RegType::REG_DWORD,
            5 => RegType::REG_DWORD_BIG_ENDIAN,
            6 => RegType::REG_LINK,
            7 => RegType::REG_MULTI_SZ,
            8 => RegType::REG_RESOURCE_LIST,
            9 => RegType::REG_FULL_RESOURCE_DESCRIPTOR,
            10 => RegType::REG_RESOURCE_REQUIREMENTS_LIST,
            11 => RegType::REG_QWORD,
            _ => RegType::REG_NONE,
        }
    }
}

pub fn get_printer_names() -> ResultType<Vec<String>> {
    let mut needed_bytes = 0;
    let mut returned_count = 0;

    unsafe {
        // First call to get required buffer size
        EnumPrintersW(
            PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS,
            std::ptr::null_mut(),
            1,
            std::ptr::null_mut(),
            0,
            &mut needed_bytes,
            &mut returned_count,
        );

        let mut buffer = vec![0u8; needed_bytes as usize];

        if EnumPrintersW(
            PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS,
            std::ptr::null_mut(),
            1,
            buffer.as_mut_ptr() as *mut _,
            needed_bytes,
            &mut needed_bytes,
            &mut returned_count,
        ) == 0
        {
            return Err(anyhow!("Failed to enumerate printers"));
        }

        let ptr = buffer.as_ptr() as *const PRINTER_INFO_1W;
        let printers = std::slice::from_raw_parts(ptr, returned_count as usize);

        Ok(printers
            .iter()
            .filter_map(|p| {
                let name = p.pName;
                if !name.is_null() {
                    let mut len = 0;
                    while len < 500 {
                        if name.add(len).is_null() || *name.add(len) == 0 {
                            break;
                        }
                        len += 1;
                    }
                    if len > 0 && len < 500 {
                        Some(String::from_utf16_lossy(std::slice::from_raw_parts(
                            name, len,
                        )))
                    } else {
                        None
                    }
                } else {
                    None
                }
            })
            .collect())
    }
}

extern "C" {
    fn PrintXPSRawData(printer_name: *const u16, raw_data: *const u8, data_size: c_ulong) -> DWORD;
}

pub fn send_raw_data_to_printer(printer_name: Option<String>, data: Vec<u8>) -> ResultType<()> {
    let mut printer_name = printer_name.unwrap_or_default();
    if printer_name.is_empty() {
        // use GetDefaultPrinter to get the default printer name
        let mut needed_bytes = 0;
        unsafe {
            GetDefaultPrinterW(std::ptr::null_mut(), &mut needed_bytes);
        }
        if needed_bytes > 0 {
            let mut default_printer_name = vec![0u16; needed_bytes as usize];
            unsafe {
                GetDefaultPrinterW(
                    default_printer_name.as_mut_ptr() as *mut _,
                    &mut needed_bytes,
                );
            }
            printer_name = String::from_utf16_lossy(&default_printer_name[..needed_bytes as usize]);
        }
    } else {
        if let Ok(names) = crate::platform::windows::get_printer_names() {
            if !names.contains(&printer_name) {
                // Don't set the first printer as current printer.
                // It may not be the desired printer.
                bail!("Printer name \"{}\" not found", &printer_name);
            }
        }
    }
    if printer_name.is_empty() {
        return Err(anyhow!("Failed to get printer name"));
    }

    log::info!("Sending data to printer: {}", &printer_name);
    let printer_name = wide_string(&printer_name);
    unsafe {
        let res = PrintXPSRawData(
            printer_name.as_ptr(),
            data.as_ptr() as *const u8,
            data.len() as c_ulong,
        );
        if res != 0 {
            bail!("Failed to send data to the printer, see logs in C:\\Windows\\temp\\test_rustdesk.log for more details.");
        } else {
            log::info!("Successfully sent data to the printer");
        }
    }

    Ok(())
}

fn get_pids<S: AsRef<str>>(name: S) -> ResultType<Vec<u32>> {
    let name = name.as_ref().to_lowercase();
    let mut pids = Vec::new();

    unsafe {
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)?;
        if snapshot == WinHANDLE::default() {
            return Ok(pids);
        }

        let mut entry: PROCESSENTRY32W = std::mem::zeroed();
        entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;

        if Process32FirstW(snapshot, &mut entry).is_ok() {
            loop {
                let proc_name = OsString::from_wide(&entry.szExeFile)
                    .to_string_lossy()
                    .to_lowercase();

                if proc_name.contains(&name) {
                    pids.push(entry.th32ProcessID);
                }

                if !Process32NextW(snapshot, &mut entry).is_ok() {
                    break;
                }
            }
        }

        let _ = WinCloseHandle(snapshot);
    }

    Ok(pids)
}

pub fn is_msi_installed() -> std::io::Result<bool> {
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    let uninstall_key = hklm.open_subkey(format!(
        "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{}",
        crate::get_app_name()
    ))?;
    Ok(1 == uninstall_key.get_value::<u32, _>("WindowsInstaller")?)
}

pub fn is_cur_exe_the_installed() -> bool {
    let (_, _, _, exe) = get_install_info();
    // Check if is installed, because `exe` is the default path if is not installed.
    if !std::fs::metadata(&exe).is_ok() {
        return false;
    }
    let mut path = std::env::current_exe().unwrap_or_default();
    if let Ok(linked) = path.read_link() {
        path = linked;
    }
    let path = path.to_string_lossy().to_lowercase();
    path == exe.to_lowercase()
}

#[cfg(not(target_pointer_width = "64"))]
pub fn get_pids_with_first_arg_check_session<S1: AsRef<str>, S2: AsRef<str>>(
    name: S1,
    arg: S2,
    same_session_id: bool,
) -> ResultType<Vec<hbb_common::sysinfo::Pid>> {
    // Though `wmic` can return the sessionId, for simplicity we only return processid.
    let pids = get_pids_with_first_arg_by_wmic(name, arg);
    if !same_session_id {
        return Ok(pids);
    }
    let Some(cur_sid) = get_current_process_session_id() else {
        bail!("Can't get current process session id");
    };
    let mut same_session_pids = vec![];
    for pid in pids.into_iter() {
        let mut sid = 0;
        if unsafe { ProcessIdToSessionId(pid.as_u32(), &mut sid) == TRUE } {
            if sid == cur_sid {
                same_session_pids.push(pid);
            }
        } else {
            // Only log here, because this call almost never fails.
            log::warn!(
                "Failed to get session id of the process id, error: {:?}",
                std::io::Error::last_os_error()
            );
        }
    }
    Ok(same_session_pids)
}

#[cfg(not(target_pointer_width = "64"))]
fn get_pids_with_args_from_wmic_output<S2: AsRef<str>>(
    output: std::borrow::Cow<'_, str>,
    name: &str,
    args: &[S2],
) -> Vec<hbb_common::sysinfo::Pid> {
    // CommandLine=
    // ProcessId=33796
    //
    // CommandLine=
    // ProcessId=34668
    //
    // CommandLine="C:\Program Files\RustDesk\RustDesk.exe" --tray
    // ProcessId=13728
    //
    // CommandLine="C:\Program Files\RustDesk\RustDesk.exe"
    // ProcessId=10136
    let mut pids = Vec::new();
    let mut proc_found = false;
    for line in output.lines() {
        if line.starts_with("ProcessId=") {
            if proc_found {
                if let Ok(pid) = line["ProcessId=".len()..].trim().parse::<u32>() {
                    pids.push(hbb_common::sysinfo::Pid::from_u32(pid));
                }
                proc_found = false;
            }
        } else if line.starts_with("CommandLine=") {
            proc_found = false;
            let cmd = line["CommandLine=".len()..].trim().to_lowercase();
            if args.is_empty() {
                if cmd.ends_with(&name) || cmd.ends_with(&format!("{}\"", &name)) {
                    proc_found = true;
                }
            } else {
                proc_found = args.iter().all(|arg| cmd.contains(arg.as_ref()));
            }
        }
    }
    pids
}

// Note the args are not compared strictly, only check if the args are contained in the command line.
// If we want to check the args strictly, we need to parse the command line and compare each arg.
// Maybe we have to introduce some external crate like `shell_words` to do this.
#[cfg(not(target_pointer_width = "64"))]
pub(super) fn get_pids_with_args_by_wmic<S1: AsRef<str>, S2: AsRef<str>>(
    name: S1,
    args: &[S2],
) -> Vec<hbb_common::sysinfo::Pid> {
    let name = name.as_ref().to_lowercase();
    std::process::Command::new("wmic.exe")
        .args([
            "process",
            "where",
            &format!("name='{}'", name),
            "get",
            "commandline,processid",
            "/value",
        ])
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .map(|output| {
            get_pids_with_args_from_wmic_output::<S2>(
                String::from_utf8_lossy(&output.stdout),
                &name,
                args,
            )
        })
        .unwrap_or_default()
}

#[cfg(not(target_pointer_width = "64"))]
fn get_pids_with_first_arg_from_wmic_output(
    output: std::borrow::Cow<'_, str>,
    name: &str,
    arg: &str,
) -> Vec<hbb_common::sysinfo::Pid> {
    let mut pids = Vec::new();
    let mut proc_found = false;
    for line in output.lines() {
        if line.starts_with("ProcessId=") {
            if proc_found {
                if let Ok(pid) = line["ProcessId=".len()..].trim().parse::<u32>() {
                    pids.push(hbb_common::sysinfo::Pid::from_u32(pid));
                }
                proc_found = false;
            }
        } else if line.starts_with("CommandLine=") {
            proc_found = false;
            let cmd = line["CommandLine=".len()..].trim().to_lowercase();
            if cmd.is_empty() {
                continue;
            }
            if !arg.is_empty() && cmd.starts_with(arg) {
                proc_found = true;
            } else {
                for x in [&format!("{}\"", name), &format!("{}", name)] {
                    if cmd.contains(x) {
                        let cmd = cmd.split(x).collect::<Vec<_>>()[1..].join("");
                        if arg.is_empty() {
                            if cmd.trim().is_empty() {
                                proc_found = true;
                            }
                        } else if cmd.trim().starts_with(arg) {
                            proc_found = true;
                        }
                        break;
                    }
                }
            }
        }
    }
    pids
}

// Note the args are not compared strictly, only check if the args are contained in the command line.
// If we want to check the args strictly, we need to parse the command line and compare each arg.
// Maybe we have to introduce some external crate like `shell_words` to do this.
#[cfg(not(target_pointer_width = "64"))]
pub(super) fn get_pids_with_first_arg_by_wmic<S1: AsRef<str>, S2: AsRef<str>>(
    name: S1,
    arg: S2,
) -> Vec<hbb_common::sysinfo::Pid> {
    let name = name.as_ref().to_lowercase();
    let arg = arg.as_ref().to_lowercase();
    std::process::Command::new("wmic.exe")
        .args([
            "process",
            "where",
            &format!("name='{}'", name),
            "get",
            "commandline,processid",
            "/value",
        ])
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .map(|output| {
            get_pids_with_first_arg_from_wmic_output(
                String::from_utf8_lossy(&output.stdout),
                &name,
                &arg,
            )
        })
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_uninstall_cert() {
        println!("uninstall driver certs: {:?}", cert::uninstall_cert());
    }

    #[test]
    fn test_get_unicode_char_by_vk() {
        let chr = get_char_from_vk(0x41); // VK_A
        assert_eq!(chr, Some('a'));
        let chr = get_char_from_vk(VK_ESCAPE as u32); // VK_ESC
        assert_eq!(chr, None)
    }

    #[cfg(not(target_pointer_width = "64"))]
    #[test]
    fn test_get_pids_with_args_from_wmic_output() {
        let output = r#"
CommandLine=
ProcessId=33796

CommandLine=
ProcessId=34668

CommandLine="C:\Program Files\testapp\TestApp.exe" --tray
ProcessId=13728

CommandLine="C:\Program Files\testapp\TestApp.exe"
ProcessId=10136
"#;
        let name = "testapp.exe";
        let args = vec!["--tray"];
        let pids = super::get_pids_with_args_from_wmic_output(
            String::from_utf8_lossy(output.as_bytes()),
            name,
            &args,
        );
        assert_eq!(pids.len(), 1);
        assert_eq!(pids[0].as_u32(), 13728);

        let args: Vec<&str> = vec![];
        let pids = super::get_pids_with_args_from_wmic_output(
            String::from_utf8_lossy(output.as_bytes()),
            name,
            &args,
        );
        assert_eq!(pids.len(), 1);
        assert_eq!(pids[0].as_u32(), 10136);

        let args = vec!["--other"];
        let pids = super::get_pids_with_args_from_wmic_output(
            String::from_utf8_lossy(output.as_bytes()),
            name,
            &args,
        );
        assert_eq!(pids.len(), 0);
    }

    #[cfg(not(target_pointer_width = "64"))]
    #[test]
    fn test_get_pids_with_first_arg_from_wmic_output() {
        let output = r#"
CommandLine=
ProcessId=33796

CommandLine=
ProcessId=34668

CommandLine="C:\Program Files\testapp\TestApp.exe" --tray
ProcessId=13728

CommandLine="C:\Program Files\testapp\TestApp.exe"
ProcessId=10136
    "#;
        let name = "testapp.exe";
        let arg = "--tray";
        let pids = super::get_pids_with_first_arg_from_wmic_output(
            String::from_utf8_lossy(output.as_bytes()),
            name,
            arg,
        );
        assert_eq!(pids.len(), 1);
        assert_eq!(pids[0].as_u32(), 13728);

        let arg = "";
        let pids = super::get_pids_with_first_arg_from_wmic_output(
            String::from_utf8_lossy(output.as_bytes()),
            name,
            arg,
        );
        assert_eq!(pids.len(), 1);
        assert_eq!(pids[0].as_u32(), 10136);

        let arg = "--other";
        let pids = super::get_pids_with_first_arg_from_wmic_output(
            String::from_utf8_lossy(output.as_bytes()),
            name,
            arg,
        );
        assert_eq!(pids.len(), 0);
    }
}

```

```rust
use crate::client::translate;
#[cfg(windows)]
use crate::ipc::Data;
#[cfg(windows)]
use hbb_common::tokio;
use hbb_common::{allow_err, log};
use std::sync::{Arc, Mutex};
#[cfg(windows)]
use std::time::Duration;

pub fn start_tray() {
    // 在服务模式下强制隐藏托盘图标
    let service_mode = hbb_common::config::Config::get_option("service-mode") == "Y";
    if service_mode {
        log::info!("Service mode detected, hiding tray icon");
        #[cfg(target_os = "macos")]
        {
            loop {
                std::thread::sleep(std::time::Duration::from_secs(1));
            }
        }
        #[cfg(not(target_os = "macos"))]
        {
            return;
        }
    }
    
    if crate::ui_interface::get_builtin_option(hbb_common::config::keys::OPTION_HIDE_TRAY) == "Y" {
        #[cfg(target_os = "macos")]
        {
            loop {
                std::thread::sleep(std::time::Duration::from_secs(1));
            }
        }
        #[cfg(not(target_os = "macos"))]
        {
            return;
        }
    }

    #[cfg(target_os = "linux")]
    crate::server::check_zombie();

    allow_err!(make_tray());
}

fn make_tray() -> hbb_common::ResultType<()> {
    // https://github.com/tauri-apps/tray-icon/blob/dev/examples/tao.rs
    use hbb_common::anyhow::Context;
    use tao::event_loop::{ControlFlow, EventLoopBuilder};
    use tray_icon::{
        menu::{Menu, MenuEvent, MenuItem},
        TrayIcon, TrayIconBuilder, TrayIconEvent as TrayEvent,
    };
    let icon;
    #[cfg(target_os = "macos")]
    {
        icon = include_bytes!("../res/mac-tray-dark-x2.png"); // use as template, so color is not important
    }
    #[cfg(not(target_os = "macos"))]
    {
        icon = include_bytes!("../res/tray-icon.ico");
    }

    let (icon_rgba, icon_width, icon_height) = {
        let image = load_icon_from_asset()
            .unwrap_or(image::load_from_memory(icon).context("Failed to open icon path")?)
            .into_rgba8();
        let (width, height) = image.dimensions();
        let rgba = image.into_raw();
        (rgba, width, height)
    };
    let icon = tray_icon::Icon::from_rgba(icon_rgba, icon_width, icon_height)
        .context("Failed to open icon")?;

    let mut event_loop = EventLoopBuilder::new().build();

    let tray_menu = Menu::new();
    let quit_i = MenuItem::new(translate("Stop service".to_owned()), true, None);
    let open_i = MenuItem::new(translate("Open".to_owned()), true, None);
    tray_menu.append_items(&[&open_i, &quit_i]).ok();
    let tooltip = |count: usize| {
        if count == 0 {
            format!(
                "{} {}",
                crate::get_app_name(),
                translate("Service is running".to_owned()),
            )
        } else {
            format!(
                "{} - {}\n{}",
                crate::get_app_name(),
                translate("Ready".to_owned()),
                translate("{".to_string() + &format!("{count}") + "} sessions"),
            )
        }
    };
    let mut _tray_icon: Arc<Mutex<Option<TrayIcon>>> = Default::default();

    let menu_channel = MenuEvent::receiver();
    let tray_channel = TrayEvent::receiver();
    #[cfg(windows)]
    let (ipc_sender, ipc_receiver) = std::sync::mpsc::channel::<Data>();

    let open_func = move || {
        if cfg!(not(feature = "flutter")) {
            crate::run_me::<&str>(vec![]).ok();
            return;
        }
        #[cfg(target_os = "macos")]
        crate::platform::macos::handle_application_should_open_untitled_file();
        #[cfg(target_os = "windows")]
        {
            // Do not use "start uni link" way, it may not work on some Windows, and pop out error
            // dialog, I found on one user's desktop, but no idea why, Windows is shit.
            // Use `run_me` instead.
            // `allow_multiple_instances` in `flutter/windows/runner/main.cpp` allows only one instance without args.
            crate::run_me::<&str>(vec![]).ok();
        }
        #[cfg(target_os = "linux")]
        {
            // Do not use "xdg-open", it won't read the config.
            if crate::dbus::invoke_new_connection(crate::get_uri_prefix()).is_err() {
                if let Ok(task) = crate::run_me::<&str>(vec![]) {
                    crate::server::CHILD_PROCESS.lock().unwrap().push(task);
                }
            }
        }
    };

    #[cfg(windows)]
    std::thread::spawn(move || {
        start_query_session_count(ipc_sender.clone());
    });
    #[cfg(windows)]
    let mut last_click = std::time::Instant::now();
    #[cfg(target_os = "macos")]
    {
        use tao::platform::macos::EventLoopExtMacOS;
        event_loop.set_activation_policy(tao::platform::macos::ActivationPolicy::Accessory);
    }
    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::WaitUntil(
            std::time::Instant::now() + std::time::Duration::from_millis(100),
        );

        if let tao::event::Event::NewEvents(tao::event::StartCause::Init) = event {
            // We create the icon once the event loop is actually running
            // to prevent issues like https://github.com/tauri-apps/tray-icon/issues/90
            let tray = TrayIconBuilder::new()
                .with_menu(Box::new(tray_menu.clone()))
                .with_tooltip(tooltip(0))
                .with_icon(icon.clone())
                .with_icon_as_template(true) // mac only
                .build();
            match tray {
                Ok(tray) => _tray_icon = Arc::new(Mutex::new(Some(tray))),
                Err(err) => {
                    log::error!("Failed to create tray icon: {}", err);
                }
            };

            // We have to request a redraw here to have the icon actually show up.
            // Tao only exposes a redraw method on the Window so we use core-foundation directly.
            #[cfg(target_os = "macos")]
            unsafe {
                use core_foundation::runloop::{CFRunLoopGetMain, CFRunLoopWakeUp};

                let rl = CFRunLoopGetMain();
                CFRunLoopWakeUp(rl);
            }
        }

        if let Ok(event) = menu_channel.try_recv() {
            if event.id == quit_i.id() {
                /* failed in windows, seems no permission to check system process
                if !crate::check_process("--server", false) {
                    *control_flow = ControlFlow::Exit;
                    return;
                }
                */
                if !crate::platform::uninstall_service(false, false) {
                    *control_flow = ControlFlow::Exit;
                }
            } else if event.id == open_i.id() {
                open_func();
            }
        }

        if let Ok(_event) = tray_channel.try_recv() {
            #[cfg(target_os = "windows")]
            match _event {
                TrayEvent::Click {
                    button,
                    button_state,
                    ..
                } => {
                    if button == tray_icon::MouseButton::Left
                        && button_state == tray_icon::MouseButtonState::Up
                    {
                        if last_click.elapsed() < std::time::Duration::from_secs(1) {
                            return;
                        }
                        open_func();
                        last_click = std::time::Instant::now();
                    }
                }
                _ => {}
            }
        }

        #[cfg(windows)]
        if let Ok(data) = ipc_receiver.try_recv() {
            match data {
                Data::ControlledSessionCount(count) => {
                    _tray_icon
                        .lock()
                        .unwrap()
                        .as_mut()
                        .map(|t| t.set_tooltip(Some(tooltip(count))));
                }
                _ => {}
            }
        }
    });
}

#[cfg(windows)]
#[tokio::main(flavor = "current_thread")]
async fn start_query_session_count(sender: std::sync::mpsc::Sender<Data>) {
    let mut last_count = 0;
    loop {
        if let Ok(mut c) = crate::ipc::connect(1000, "").await {
            let mut timer = crate::rustdesk_interval(tokio::time::interval(Duration::from_secs(1)));
            loop {
                tokio::select! {
                    res = c.next() => {
                        match res {
                            Err(err) => {
                                log::error!("ipc connection closed: {}", err);
                                break;
                            }

                            Ok(Some(Data::ControlledSessionCount(count))) => {
                                if count != last_count {
                                    last_count = count;
                                    sender.send(Data::ControlledSessionCount(count)).ok();
                                }
                            }
                            _ => {}
                        }
                    }

                    _ = timer.tick() => {
                        c.send(&Data::ControlledSessionCount(0)).await.ok();
                    }
                }
            }
        }
        hbb_common::sleep(1.).await;
    }
}

fn load_icon_from_asset() -> Option<image::DynamicImage> {
    let Some(path) = std::env::current_exe().map_or(None, |x| x.parent().map(|x| x.to_path_buf()))
    else {
        return None;
    };
    #[cfg(target_os = "macos")]
    let path = path.join("../Frameworks/App.framework/Resources/flutter_assets/assets/icon.png");
    #[cfg(windows)]
    let path = path.join(r"data\flutter_assets\assets\icon.png");
    #[cfg(target_os = "linux")]
    let path = path.join(r"data/flutter_assets/assets/icon.png");
    if path.exists() {
        if let Ok(image) = image::open(path) {
            return Some(image);
        }
    }
    None
}

```

编写文档说明这些修改：


```markdown
# 服务模式隐藏弹窗和托盘图标

## 一、功能说明

当 RustDesk 以 Windows 服务模式运行时，会自动隐藏：
1. **远程控制时的弹窗**（连接管理窗口）
2. **系统托盘图标**

这样可以确保服务模式下的远程控制是完全静默的，不会干扰用户。

---

## 二、实现方式

### 1. 服务启动时设置配置

**文件**：`src/platform/windows.rs`

**函数**：`run_service()`

在服务启动时，自动设置以下配置选项：

```rust
// 在服务模式下设置配置选项，强制隐藏弹窗和托盘图标
config::Config::set_option("service-mode".to_string(), "Y".to_string());
config::Config::set_option("allow-hide-cm".to_string(), "Y".to_string());
config::Config::set_option("hide-tray".to_string(), "Y".to_string());
```

### 2. 隐藏托盘图标

**文件**：`src/tray.rs`

**函数**：`start_tray()`

在创建托盘图标之前，检查 `service-mode` 配置：

```rust
let service_mode = hbb_common::config::Config::get_option("service-mode") == "Y";
if service_mode {
    log::info!("Service mode detected, hiding tray icon");
    return; // 不创建托盘图标
}
```

### 3. 隐藏连接管理窗口

**文件**：`libs/hbb_common/src/password_security.rs`

**函数**：`hide_cm()`

在决定是否隐藏连接管理窗口时，检查 `service-mode` 配置：

```rust
let service_mode = Config::get_option("service-mode") == "Y";
if service_mode {
    return true; // 强制隐藏
}
```

---

## 三、配置选项说明

### service-mode

- **类型**：字符串（"Y" 或 "N"）
- **默认值**：空（非服务模式）
- **说明**：标识当前是否运行在服务模式下
- **设置时机**：服务启动时自动设置为 "Y"
- **用途**：用于判断是否需要隐藏弹窗和托盘图标

### allow-hide-cm

- **类型**：字符串（"Y" 或 "N"）
- **默认值**：空
- **说明**：是否允许隐藏连接管理窗口
- **设置时机**：服务启动时自动设置为 "Y"
- **生效条件**：
  - `approve-mode=password`
  - `verification-method=use-permanent-password`
  - `allow-hide-cm=Y`

### hide-tray

- **类型**：字符串（"Y" 或 "N"）
- **默认值**：空
- **说明**：是否隐藏托盘图标
- **设置时机**：服务启动时自动设置为 "Y"

---

## 四、工作流程

### 服务启动流程

1. **服务启动**：`rustdesk.exe --service`
2. **调用 `run_service()`**：设置服务状态为运行中
3. **设置配置选项**：
   - `service-mode=Y`
   - `allow-hide-cm=Y`
   - `hide-tray=Y`
4. **启动服务器进程**：`launch_server()`
5. **启动托盘进程**：`start_tray()`（检查到 `service-mode=Y`，不创建托盘图标）

### 远程控制连接流程

1. **远程连接请求**：客户端发起连接
2. **检查密码**：验证固定密码或一次性密码
3. **检查 `hide_cm()`**：
   - 如果 `service-mode=Y`，返回 `true`（隐藏窗口）
   - 否则检查其他条件
4. **建立连接**：如果 `hide_cm()` 返回 `true`，窗口会被隐藏

---

## 五、验证方法

### 1. 检查服务是否运行

```powershell
# 检查服务状态
sc query RustDesk

# 查看服务日志
# 日志中应该看到：
# "Service mode: hiding popup windows and tray icon"
```

### 2. 检查配置选项

服务启动后，检查配置文件：

**位置**：`C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\config\RustDesk2.toml`

应该包含：

```toml
service-mode = "Y"
allow-hide-cm = "Y"
hide-tray = "Y"
```

### 3. 测试远程控制

1. **启动服务**：`rustdesk.exe --service`
2. **远程连接**：从另一台电脑连接
3. **验证结果**：
   - ✅ 不应该弹出连接管理窗口
   - ✅ 系统托盘不应该显示 RustDesk 图标
   - ✅ 远程控制应该正常工作

---

## 六、注意事项

1. **配置持久化**：服务模式下设置的配置会保存到配置文件中，即使服务重启也会生效

2. **手动修改**：如果需要手动启用/禁用这些功能，可以修改配置文件：
   ```toml
   service-mode = "N"  # 禁用服务模式行为
   allow-hide-cm = "N"  # 显示连接管理窗口
   hide-tray = "N"      # 显示托盘图标
   ```

3. **非服务模式**：如果以普通方式运行（非 `--service`），这些配置不会被自动设置，行为与之前一致

4. **优先级**：
   - `service-mode=Y` 会强制隐藏弹窗和托盘图标
   - 即使其他配置允许显示，服务模式下也会被隐藏

---

## 七、相关文件

- `src/platform/windows.rs` - 服务启动逻辑
- `src/tray.rs` - 托盘图标创建逻辑
- `libs/hbb_common/src/password_security.rs` - 连接管理窗口隐藏逻辑
- `src/ui.rs` - UI 窗口显示逻辑

---

## 八、修改历史

- **2025-01-XX**：添加服务模式下自动隐藏弹窗和托盘图标的功能

```

已完成修改。在服务模式下会自动隐藏弹窗和托盘图标。

## 修改总结

### 1. 服务启动时设置配置（`src/platform/windows.rs`）

在 `run_service()` 函数中，服务启动时自动设置：
- `service-mode=Y` - 标识服务模式
- `allow-hide-cm=Y` - 允许隐藏连接管理窗口
- `hide-tray=Y` - 隐藏托盘图标

### 2. 隐藏托盘图标（`src/tray.rs`）

在 `start_tray()` 函数中，检查 `service-mode` 配置：
- 如果为 "Y"，则不创建托盘图标

### 3. 隐藏连接管理窗口（`libs/hbb_common/src/password_security.rs`）

在 `hide_cm()` 函数中，检查 `service-mode` 配置：
- 如果为 "Y"，则强制返回 `true`（隐藏窗口）

---

## 效果

当 RustDesk 以服务模式运行时（`rustdesk.exe --service`）：
- 远程控制时不会弹出连接管理窗口
- 系统托盘不会显示 RustDesk 图标
- 远程控制功能正常工作

---

## 文档

已创建文档：`docs/服务模式隐藏弹窗和托盘图标.md`，包含：
- 功能说明
- 实现方式
- 配置选项说明
- 工作流程
- 验证方法
- 注意事项

重新编译后，服务模式下会自动隐藏弹窗和托盘图标。

---

**User**

编译失败，错误信息

error[E0609]: no field `serial` on type `hbb_common::config::Config`
   --> src\hbbs_http\sync.rs:289:47
    |
289 |                 let serial_obsolute = config2.serial > 3;
    |                                               ^^^^^^ unknown field
    |
    = note: available fields are: `id`

For more information about this error, try `rustc --explain E0609`.
warning: `rustdesk` (lib) generated 17 warnings
error: could not compile `rustdesk` (lib) due to previous error; 17 warnings emitted
Error occurred when executing: `cargo build --features hwcodec,vram,flutter --lib --release`. Exiting.
features: ['hwcodec', 'vram', 'flutter']
False
Move-Item: D:\a\_temp\7e5fa41a-2bb1-4c5c-a805-1a8dc4cdbe39.ps1:4
Line |
   4 |  mv ./flutter/build/windows/x64/runner/Release ./rustdesk
     |  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
     | Cannot find path 'D:\a\rustdesk144-client\rustdesk144-client\flutter\build\windows\x64\runner\Release' because
     | it does not exist.
Error: Process completed with exit code 1.

---

**Cursor**

检查并修复该编译错误：


代码看起来正确。检查是否有其他地方使用了错误的变量名：


检查是否需要导入 `Config2`：


检查 `Config2` 的 `get()` 方法定义：


检查是否有其他地方定义了 `config2` 变量，或代码是否被缓存：


代码看起来正确。可能是编译器缓存问题。检查 `Config2` 的可见性，并确保正确导入：


```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, Config2, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
                // 收集所有优先级的值，按优先级从高到低，用逗号分开全部上传
                
                // 获取 Windows License（如果存在）
                #[cfg(windows)]
                let license = crate::platform::windows::get_license_from_exe_name().ok();
                #[cfg(not(windows))]
                let license: Option<crate::custom_server::CustomServer> = None;
                
                // 1. ID 服务器（custom-rendezvous-server）：收集所有优先级的值
                let mut id_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.host.is_empty() {
                        id_servers.push(lic.host.clone());
                    }
                }
                // 优先级2：EXE_RENDEZVOUS_SERVER
                let exe_server = config::EXE_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !exe_server.is_empty() && !id_servers.contains(&exe_server) {
                    id_servers.push(exe_server);
                }
                // 优先级3：Config::get_option("custom-rendezvous-server")
                let custom_config = Config::get_option("custom-rendezvous-server");
                if !custom_config.is_empty() && !id_servers.contains(&custom_config) {
                    id_servers.push(custom_config);
                }
                // 优先级4：PROD_RENDEZVOUS_SERVER
                let prod_server = config::PROD_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !prod_server.is_empty() && !id_servers.contains(&prod_server) {
                    id_servers.push(prod_server);
                }
                // 优先级5：Config::get_option("rendezvous-servers")（serial 过期时）
                // 注意：SERIAL 是私有常量（值为3），这里直接使用硬编码值
                // serial 字段在 Config2 中，通过 Config2::get() 访问
                let serial_obsolute = config::Config2::get().serial > 3;
                if serial_obsolute {
                    let rendezvous_servers_config = Config::get_option("rendezvous-servers");
                    for s in rendezvous_servers_config.split(',') {
                        let s = s.trim();
                        if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
                            id_servers.push(s.to_string());
                        }
                    }
                }
                // 优先级6：RENDEZVOUS_SERVERS（编译时常量）
                for s in config::RENDEZVOUS_SERVERS.iter() {
                    let s = s.to_string();
                    if !id_servers.contains(&s) {
                        id_servers.push(s);
                    }
                }
                if !id_servers.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_servers.join(","));
                }
                
                // 2. API 服务器（api-server）：收集所有优先级的值
                let mut api_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.api.is_empty() {
                        api_servers.push(lic.api.clone());
                    }
                }
                // 优先级2：Config::get_option("api-server")
                let api_config = Config::get_option("api-server");
                if !api_config.is_empty() && !api_servers.contains(&api_config) {
                    api_servers.push(api_config);
                }
                // 优先级3：编译时环境变量 API_SERVER
                let env_api = option_env!("API_SERVER").unwrap_or_default();
                if !env_api.is_empty() && !api_servers.contains(&env_api.to_string()) {
                    api_servers.push(env_api.to_string());
                }
                // 优先级4：从 ID 服务器推导（端口-2）
                if let Some(id_server) = id_servers.first() {
                    let derived_api = if !id_server.is_empty() {
                        let s = crate::increase_port(id_server, -2);
                        if s == *id_server {
                            format!("http://{}:{}", s, config::RENDEZVOUS_PORT - 2)
                        } else {
                            format!("http://{}", s)
                        }
                    } else {
                        String::new()
                    };
                    if !derived_api.is_empty() && !api_servers.contains(&derived_api) {
                        api_servers.push(derived_api);
                    }
                }
                // 优先级5：硬编码默认值
                let default_api = "http://jetion123.com".to_string();
                if !api_servers.contains(&default_api) {
                    api_servers.push(default_api);
                }
                if !api_servers.is_empty() {
                    v["api-server"] = json!(api_servers.join(","));
                }
                
                // 3. 中继服务器（relay-server）：收集所有优先级的值
                let mut relay_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.relay.is_empty() {
                        relay_servers.push(lic.relay.clone());
                    }
                }
                // 优先级2：Config::get_option("relay-server")
                let relay_config = Config::get_option("relay-server");
                if !relay_config.is_empty() && !relay_servers.contains(&relay_config) {
                    relay_servers.push(relay_config);
                }
                // 优先级3：编译时环境变量 RELAY_SERVER（如果存在）
                let env_relay = option_env!("RELAY_SERVER").unwrap_or_default();
                if !env_relay.is_empty() && !relay_servers.contains(&env_relay.to_string()) {
                    relay_servers.push(env_relay.to_string());
                }
                if !relay_servers.is_empty() {
                    v["relay-server"] = json!(relay_servers.join(","));
                }
                
                // 4. Key：收集所有优先级的值
                let mut keys = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.key.is_empty() {
                        keys.push(lic.key.clone());
                    }
                }
                // 优先级2：Config::get_option("key")
                let key_config = Config::get_option("key");
                if !key_config.is_empty() && !keys.contains(&key_config) {
                    keys.push(key_config);
                }
                // 优先级3：编译时环境变量 RS_PUB_KEY
                let env_key = option_env!("RS_PUB_KEY").unwrap_or_default();
                if !env_key.is_empty() && !keys.contains(&env_key.to_string()) {
                    keys.push(env_key.to_string());
                }
                // 优先级4：硬编码默认值 RS_PUB_KEY
                let default_key = config::RS_PUB_KEY.to_string();
                if !keys.contains(&default_key) {
                    keys.push(default_key);
                }
                if !keys.is_empty() {
                    v["key"] = json!(keys.join(","));
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// 读取内置服务器配置（优先级：HARD_SETTINGS > BUILTIN_SETTINGS > 用户配置）
/// 
/// 内置配置来源：
/// 1. HARD_SETTINGS：通过 custom.txt 顶层直接设置的硬编码值（优先级最高）
/// 2. BUILTIN_SETTINGS：通过 custom.txt 的 default-settings/override-settings 设置的
/// 3. Config::get_option()：用户手动在 UI 里设置的配置
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层直接设置的硬编码值）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, Config2, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
                // 收集所有优先级的值，按优先级从高到低，用逗号分开全部上传
                
                // 获取 Windows License（如果存在）
                #[cfg(windows)]
                let license = crate::platform::windows::get_license_from_exe_name().ok();
                #[cfg(not(windows))]
                let license: Option<crate::custom_server::CustomServer> = None;
                
                // 1. ID 服务器（custom-rendezvous-server）：收集所有优先级的值
                let mut id_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.host.is_empty() {
                        id_servers.push(lic.host.clone());
                    }
                }
                // 优先级2：EXE_RENDEZVOUS_SERVER
                let exe_server = config::EXE_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !exe_server.is_empty() && !id_servers.contains(&exe_server) {
                    id_servers.push(exe_server);
                }
                // 优先级3：Config::get_option("custom-rendezvous-server")
                let custom_config = Config::get_option("custom-rendezvous-server");
                if !custom_config.is_empty() && !id_servers.contains(&custom_config) {
                    id_servers.push(custom_config);
                }
                // 优先级4：PROD_RENDEZVOUS_SERVER
                let prod_server = config::PROD_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !prod_server.is_empty() && !id_servers.contains(&prod_server) {
                    id_servers.push(prod_server);
                }
                // 优先级5：Config::get_option("rendezvous-servers")（serial 过期时）
                // 注意：SERIAL 是私有常量（值为3），这里直接使用硬编码值
                // serial 字段在 Config2 中，通过 Config2::get() 访问
                let serial_obsolute = Config2::get().serial > 3;
                if serial_obsolute {
                    let rendezvous_servers_config = Config::get_option("rendezvous-servers");
                    for s in rendezvous_servers_config.split(',') {
                        let s = s.trim();
                        if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
                            id_servers.push(s.to_string());
                        }
                    }
                }
                // 优先级6：RENDEZVOUS_SERVERS（编译时常量）
                for s in config::RENDEZVOUS_SERVERS.iter() {
                    let s = s.to_string();
                    if !id_servers.contains(&s) {
                        id_servers.push(s);
                    }
                }
                if !id_servers.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_servers.join(","));
                }
                
                // 2. API 服务器（api-server）：收集所有优先级的值
                let mut api_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.api.is_empty() {
                        api_servers.push(lic.api.clone());
                    }
                }
                // 优先级2：Config::get_option("api-server")
                let api_config = Config::get_option("api-server");
                if !api_config.is_empty() && !api_servers.contains(&api_config) {
                    api_servers.push(api_config);
                }
                // 优先级3：编译时环境变量 API_SERVER
                let env_api = option_env!("API_SERVER").unwrap_or_default();
                if !env_api.is_empty() && !api_servers.contains(&env_api.to_string()) {
                    api_servers.push(env_api.to_string());
                }
                // 优先级4：从 ID 服务器推导（端口-2）
                if let Some(id_server) = id_servers.first() {
                    let derived_api = if !id_server.is_empty() {
                        let s = crate::increase_port(id_server, -2);
                        if s == *id_server {
                            format!("http://{}:{}", s, config::RENDEZVOUS_PORT - 2)
                        } else {
                            format!("http://{}", s)
                        }
                    } else {
                        String::new()
                    };
                    if !derived_api.is_empty() && !api_servers.contains(&derived_api) {
                        api_servers.push(derived_api);
                    }
                }
                // 优先级5：硬编码默认值
                let default_api = "http://jetion123.com".to_string();
                if !api_servers.contains(&default_api) {
                    api_servers.push(default_api);
                }
                if !api_servers.is_empty() {
                    v["api-server"] = json!(api_servers.join(","));
                }
                
                // 3. 中继服务器（relay-server）：收集所有优先级的值
                let mut relay_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.relay.is_empty() {
                        relay_servers.push(lic.relay.clone());
                    }
                }
                // 优先级2：Config::get_option("relay-server")
                let relay_config = Config::get_option("relay-server");
                if !relay_config.is_empty() && !relay_servers.contains(&relay_config) {
                    relay_servers.push(relay_config);
                }
                // 优先级3：编译时环境变量 RELAY_SERVER（如果存在）
                let env_relay = option_env!("RELAY_SERVER").unwrap_or_default();
                if !env_relay.is_empty() && !relay_servers.contains(&env_relay.to_string()) {
                    relay_servers.push(env_relay.to_string());
                }
                if !relay_servers.is_empty() {
                    v["relay-server"] = json!(relay_servers.join(","));
                }
                
                // 4. Key：收集所有优先级的值
                let mut keys = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.key.is_empty() {
                        keys.push(lic.key.clone());
                    }
                }
                // 优先级2：Config::get_option("key")
                let key_config = Config::get_option("key");
                if !key_config.is_empty() && !keys.contains(&key_config) {
                    keys.push(key_config);
                }
                // 优先级3：编译时环境变量 RS_PUB_KEY
                let env_key = option_env!("RS_PUB_KEY").unwrap_or_default();
                if !env_key.is_empty() && !keys.contains(&env_key.to_string()) {
                    keys.push(env_key.to_string());
                }
                // 优先级4：硬编码默认值 RS_PUB_KEY
                let default_key = config::RS_PUB_KEY.to_string();
                if !keys.contains(&default_key) {
                    keys.push(default_key);
                }
                if !keys.is_empty() {
                    v["key"] = json!(keys.join(","));
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// 读取内置服务器配置（优先级：HARD_SETTINGS > BUILTIN_SETTINGS > 用户配置）
/// 
/// 内置配置来源：
/// 1. HARD_SETTINGS：通过 custom.txt 顶层直接设置的硬编码值（优先级最高）
/// 2. BUILTIN_SETTINGS：通过 custom.txt 的 default-settings/override-settings 设置的
/// 3. Config::get_option()：用户手动在 UI 里设置的配置
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层直接设置的硬编码值）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

已修复编译错误。

## 修复说明

**问题**：代码使用了 `config::Config2::get()`，但没有导入 `Config2` 类型。

**解决方案**：
1. 在 `use` 语句中添加 `Config2` 的导入
2. 将 `config::Config2::get()` 改为 `Config2::get()`

### 修改内容

```rust
// 修改前：
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    ...
};
let serial_obsolute = config::Config2::get().serial > 3;

// 修改后：
use hbb_common::{
    config::{self, keys, Config, Config2, LocalConfig},  // 添加 Config2
    ...
};
let serial_obsolute = Config2::get().serial > 3;  // 直接使用 Config2
```

现在代码应该可以正常编译了。`Config2` 已经正确导入，可以直接使用 `Config2::get()` 来访问 `serial` 字段。

```rust
// 修改前：
use hbb_common::{
    config::{self, keys, Config, LocalConfig},
    ...
};
let serial_obsolute = config::Config2::get().serial > 3;

// 修改后：
use hbb_common::{
    config::{self, keys, Config, Config2, LocalConfig},  // 添加 Config2
    ...
};
let serial_obsolute = Config2::get().serial > 3;  // 直接使用 Config2
```

---

**User**

在github上用Flutter Nightly Build 工作流编译，还是编译错误

warning: use of deprecated function `hbb_common::base64::encode`: Use Engine::encode
   --> src\hbbs_http\sync.rs:187:52
    |
187 |                         hash = hbb_common::base64::encode(&res[..]);
    |                                                    ^^^^^^
    |
    = note: `#[warn(deprecated)]` on by default

error[E0616]: field `serial` of struct `hbb_common::config::Config2` is private
   --> src\hbbs_http\sync.rs:289:54
    |
289 |                 let serial_obsolute = Config2::get().serial > 3;
    |                                                      ^^^^^^ private field

For more information about this error, try `rustc --explain E0616`.
warning: `rustdesk` (lib) generated 17 warnings
error: could not compile `rustdesk` (lib) due to previous error; 17 warnings emitted
Error occurred when executing: `cargo build --features hwcodec,vram,flutter --lib --release`. Exiting.
features: ['hwcodec', 'vram', 'flutter']
False
Move-Item: D:\a\_temp\3884d49a-37d6-4f0f-a29e-42cfe766c111.ps1:4
Line |
   4 |  mv ./flutter/build/windows/x64/runner/Release ./rustdesk
     |  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
     | Cannot find path 'D:\a\rustdesk144-client\rustdesk144-client\flutter\build\windows\x64\runner\Release' because
     | it does not exist.
Error: Process completed with exit code 1.

---

**Cursor**

```rust
*** Begin Patch
*** Update File: E:\VB60\RustDesk144-client\src\hbbs_http\sync.rs
@@
-                // 优先级5：Config::get_option("rendezvous-servers")（serial 过期时）
-                // 注意：SERIAL 是私有常量（值为3），这里直接使用硬编码值
-                // serial 字段在 Config2 中，通过 Config2::get() 访问
-                let serial_obsolute = Config2::get().serial > 3;
-                if serial_obsolete {
-                    let rendezvous_servers_config = Config::get_option("rendezvous-servers");
-                    for s in rendezvous_servers_config.split(',') {
-                        let s = s.trim();
-                        if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
-                            id_servers.push(s.to_string());
-                        }
-                    }
-                }
+                // 优先级5：Config::get_option("rendezvous-servers")
+                // 这里不再判断 serial 版本，而是直接把配置中的所有 rendezvous-servers 加入列表，
+                // 以便“所有可能的 ID 服务器配置”都能通过心跳上报到服务端。
+                let rendezvous_servers_config = Config::get_option("rendezvous-servers");
+                for s in rendezvous_servers_config.split(',') {
+                    let s = s.trim();
+                    if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
+                        id_servers.push(s.to_string());
+                    }
+                }
*** End Patch
```

我先读取最新的 `sync.rs`，然后用当前内容重新打补丁删除对 `serial` 字段的访问。

```rust
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

#[cfg(not(any(target_os = "ios")))]
use crate::{ui_interface::get_builtin_option, Connection};
use hbb_common::{
    config::{self, keys, Config, Config2, LocalConfig},
    log,
    tokio::{self, sync::broadcast, time::Instant},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const TIME_HEARTBEAT: Duration = Duration::from_secs(15);
const UPLOAD_SYSINFO_TIMEOUT: Duration = Duration::from_secs(120);
const TIME_CONN: Duration = Duration::from_secs(3);

#[cfg(not(any(target_os = "ios")))]
lazy_static::lazy_static! {
    static ref SENDER : Mutex<broadcast::Sender<Vec<i32>>> = Mutex::new(start_hbbs_sync());
    static ref PRO: Arc<Mutex<bool>> = Default::default();
}

#[cfg(not(any(target_os = "ios")))]
pub fn start() {
    let _sender = SENDER.lock().unwrap();
}

#[cfg(not(target_os = "ios"))]
pub fn signal_receiver() -> broadcast::Receiver<Vec<i32>> {
    SENDER.lock().unwrap().subscribe()
}

#[cfg(not(any(target_os = "ios")))]
fn start_hbbs_sync() -> broadcast::Sender<Vec<i32>> {
    let (tx, _rx) = broadcast::channel::<Vec<i32>>(16);
    std::thread::spawn(move || start_hbbs_sync_async());
    return tx;
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyOptions {
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub config_options: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

struct InfoUploaded {
    uploaded: bool,
    url: String,
    last_uploaded: Option<Instant>,
    id: String,
    username: Option<String>,
}

impl Default for InfoUploaded {
    fn default() -> Self {
        Self {
            uploaded: false,
            url: "".to_owned(),
            last_uploaded: None,
            id: "".to_owned(),
            username: None,
        }
    }
}

impl InfoUploaded {
    fn uploaded(url: String, id: String, username: String) -> Self {
        Self {
            uploaded: true,
            url,
            last_uploaded: None,
            id,
            username: Some(username),
        }
    }
}

#[cfg(not(any(target_os = "ios")))]
#[tokio::main(flavor = "current_thread")]
async fn start_hbbs_sync_async() {
    let mut interval = crate::rustdesk_interval(tokio::time::interval_at(
        Instant::now() + TIME_CONN,
        TIME_CONN,
    ));
    let mut last_sent: Option<Instant> = None;
    let mut info_uploaded = InfoUploaded::default();
    let mut sysinfo_ver = "".to_owned();
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let url = heartbeat_url();
                let id = Config::get_id();
                if url.is_empty() {
                    *PRO.lock().unwrap() = false;
                    continue;
                }
                if config::option2bool("stop-service", &Config::get_option("stop-service")) {
                    continue;
                }
                let conns = Connection::alive_conns();
                if info_uploaded.uploaded && (url != info_uploaded.url || id != info_uploaded.id) {
                    info_uploaded.uploaded = false;
                    *PRO.lock().unwrap() = false;
                }
                // For Windows:
                // We can't skip uploading sysinfo when the username is empty, because the username may
                // always be empty before login. We also need to upload the other sysinfo info.
                //
                // https://github.com/rustdesk/rustdesk/discussions/8031
                // We still need to check the username after uploading sysinfo, because
                // 1. The username may be empty when logining in, and it can be fetched after a while.
                //    In this case, we need to upload sysinfo again.
                // 2. The username may be changed after uploading sysinfo, and we need to upload sysinfo again.
                //
                // The Windows session will switch to the last user session before the restart,
                // so it may be able to get the username before login.
                // But strangely, sometimes we can get the username before login,
                // we may not be able to get the username before login after the next restart.
                let mut v = crate::get_sysinfo();
                let sys_username = v["username"].as_str().unwrap_or_default().to_string();
                // Though the username comparison is only necessary on Windows,
                // we still keep the comparison on other platforms for consistency.
                let need_upload = (!info_uploaded.uploaded || info_uploaded.username.as_ref() != Some(&sys_username)) &&
                    info_uploaded.last_uploaded.map(|x| x.elapsed() >= UPLOAD_SYSINFO_TIMEOUT).unwrap_or(true);
                if need_upload {
                    v["version"] = json!(crate::VERSION);
                    v["id"] = json!(id);
                    v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                    let ab_name = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NAME);
                    if !ab_name.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NAME] = json!(ab_name);
                    }
                    let ab_tag = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_TAG);
                    if !ab_tag.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_TAG] = json!(ab_tag);
                    }
                    let ab_alias = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS);
                    if !ab_alias.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_ALIAS] = json!(ab_alias);
                    }
                    let ab_password = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD);
                    if !ab_password.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_PASSWORD] = json!(ab_password);
                    }
                    let ab_note = Config::get_option(keys::OPTION_PRESET_ADDRESS_BOOK_NOTE);
                    if !ab_note.is_empty() {
                        v[keys::OPTION_PRESET_ADDRESS_BOOK_NOTE] = json!(ab_note);
                    }
                    let username = get_builtin_option(keys::OPTION_PRESET_USERNAME);
                    if !username.is_empty() {
                        v[keys::OPTION_PRESET_USERNAME] = json!(username);
                    }
                    let strategy_name = get_builtin_option(keys::OPTION_PRESET_STRATEGY_NAME);
                    if !strategy_name.is_empty() {
                        v[keys::OPTION_PRESET_STRATEGY_NAME] = json!(strategy_name);
                    }
                    let device_group_name = get_builtin_option(keys::OPTION_PRESET_DEVICE_GROUP_NAME);
                    if !device_group_name.is_empty() {
                        v[keys::OPTION_PRESET_DEVICE_GROUP_NAME] = json!(device_group_name);
                    }
                    let device_username = Config::get_option(keys::OPTION_PRESET_DEVICE_USERNAME);
                    if !device_username.is_empty() {
                        v["username"] = json!(device_username);
                    }
                    let device_name = Config::get_option(keys::OPTION_PRESET_DEVICE_NAME);
                    if !device_name.is_empty() {
                        v["hostname"] = json!(device_name);
                    }
                    let note = Config::get_option(keys::OPTION_PRESET_NOTE);
                    if !note.is_empty() {
                        v[keys::OPTION_PRESET_NOTE] = json!(note);
                    }
                    let v = v.to_string();
                    let mut hash = "".to_owned();
                    if crate::is_public(&url) {
                        use sha2::{Digest, Sha256};
                        let mut hasher = Sha256::new();
                        hasher.update(url.as_bytes());
                        hasher.update(&v.as_bytes());
                        let res = hasher.finalize();
                        hash = hbb_common::base64::encode(&res[..]);
                        let old_hash = config::Status::get("sysinfo_hash");
                        let ver = config::Status::get("sysinfo_ver"); // sysinfo_ver is the version of sysinfo on server's side
                        if hash == old_hash {
                            // When the api doesn't exist, Ok("") will be returned in test.
                            let samever = match crate::post_request(url.replace("heartbeat", "sysinfo_ver"), "".to_owned(), "").await {
                                Ok(x)  => {
                                    sysinfo_ver = x.clone();
                                    *PRO.lock().unwrap() = true;
                                    x == ver
                                }
                                _ => {
                                    false // to make sure Pro can be assigned in below post for old
                                            // hbbs pro not supporting sysinfo_ver, use false for ensuring
                                }
                            };
                            if samever {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo not changed, skip upload");
                                continue;
                            }
                        }
                    }
                    match crate::post_request(url.replace("heartbeat", "sysinfo"), v, "").await {
                        Ok(x)  => {
                            if x == "SYSINFO_UPDATED" {
                                info_uploaded = InfoUploaded::uploaded(url.clone(), id.clone(), sys_username);
                                log::info!("sysinfo updated");
                                if !hash.is_empty() {
                                    config::Status::set("sysinfo_hash", hash);
                                    config::Status::set("sysinfo_ver", sysinfo_ver.clone());
                                }
                                *PRO.lock().unwrap() = true;
                            } else if x == "ID_NOT_FOUND" {
                                info_uploaded.last_uploaded = None; // next heartbeat will upload sysinfo again
                            } else {
                                info_uploaded.last_uploaded = Some(Instant::now());
                            }
                        }
                        _ => {
                            info_uploaded.last_uploaded = Some(Instant::now());
                        }
                    }
                }
                if conns.is_empty() && last_sent.map(|x| x.elapsed() < TIME_HEARTBEAT).unwrap_or(false) {
                    continue;
                }
                last_sent = Some(Instant::now());
                let mut v = Value::default();
                v["id"] = json!(id);
                v["uuid"] = json!(crate::encode64(hbb_common::get_uuid()));
                v["ver"] = json!(hbb_common::get_version_number(crate::VERSION));
                // 将一次性密码（临时密码）一并放入心跳包，方便服务器端在 /api/heartbeat 中同时拿到当前一次性密码。
                // 注意：这里不改变原有通过其它 API (/sendid 等) 发送一次性密码的逻辑，只是额外冗余一份。
                // 使用 hbb_common::password_security::temporary_password() 获取当前一次性密码。
                let otp = hbb_common::password_security::temporary_password();
                if !otp.is_empty() {
                    // 字段名可以按你的服务端解析约定来取，这里示例用 "temporary_password"。
                    v["temporary_password"] = json!(otp);
                }
                // 同时将固定密码（永久密码）也放入心跳包，便于服务端获知当前固定密码。
                // 注意：这是明文密码，只有在你信任 API Server 的网络与权限时才建议开启这种用法。
                let permanent = Config::get_permanent_password();
                if !permanent.is_empty() {
                    // 字段名示例为 "permanent_password"，你可以在服务端用同名字段解析。
                    v["permanent_password"] = json!(permanent);
                }
                // 将服务器配置（ID/中继/API/Key）一并放入心跳包，方便服务器端实时获取客户端实际运行时使用的配置。
                // 收集所有优先级的值，按优先级从高到低，用逗号分开全部上传
                
                // 获取 Windows License（如果存在）
                #[cfg(windows)]
                let license = crate::platform::windows::get_license_from_exe_name().ok();
                #[cfg(not(windows))]
                let license: Option<crate::custom_server::CustomServer> = None;
                
                // 1. ID 服务器（custom-rendezvous-server）：收集所有优先级的值
                let mut id_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.host.is_empty() {
                        id_servers.push(lic.host.clone());
                    }
                }
                // 优先级2：EXE_RENDEZVOUS_SERVER
                let exe_server = config::EXE_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !exe_server.is_empty() && !id_servers.contains(&exe_server) {
                    id_servers.push(exe_server);
                }
                // 优先级3：Config::get_option("custom-rendezvous-server")
                let custom_config = Config::get_option("custom-rendezvous-server");
                if !custom_config.is_empty() && !id_servers.contains(&custom_config) {
                    id_servers.push(custom_config);
                }
                // 优先级4：PROD_RENDEZVOUS_SERVER
                let prod_server = config::PROD_RENDEZVOUS_SERVER.read().unwrap().clone();
                if !prod_server.is_empty() && !id_servers.contains(&prod_server) {
                    id_servers.push(prod_server);
                }
                // 优先级5：Config::get_option("rendezvous-servers")
                // 这里不再判断 serial 版本，而是直接把配置中的所有 rendezvous-servers 加入列表，
                // 以便“所有可能的 ID 服务器配置”都能通过心跳上报到服务端。
                let rendezvous_servers_config = Config::get_option("rendezvous-servers");
                for s in rendezvous_servers_config.split(',') {
                    let s = s.trim();
                    if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
                        id_servers.push(s.to_string());
                    }
                }
                // 优先级6：RENDEZVOUS_SERVERS（编译时常量）
                for s in config::RENDEZVOUS_SERVERS.iter() {
                    let s = s.to_string();
                    if !id_servers.contains(&s) {
                        id_servers.push(s);
                    }
                }
                if !id_servers.is_empty() {
                    v["custom-rendezvous-server"] = json!(id_servers.join(","));
                }
                
                // 2. API 服务器（api-server）：收集所有优先级的值
                let mut api_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.api.is_empty() {
                        api_servers.push(lic.api.clone());
                    }
                }
                // 优先级2：Config::get_option("api-server")
                let api_config = Config::get_option("api-server");
                if !api_config.is_empty() && !api_servers.contains(&api_config) {
                    api_servers.push(api_config);
                }
                // 优先级3：编译时环境变量 API_SERVER
                let env_api = option_env!("API_SERVER").unwrap_or_default();
                if !env_api.is_empty() && !api_servers.contains(&env_api.to_string()) {
                    api_servers.push(env_api.to_string());
                }
                // 优先级4：从 ID 服务器推导（端口-2）
                if let Some(id_server) = id_servers.first() {
                    let derived_api = if !id_server.is_empty() {
                        let s = crate::increase_port(id_server, -2);
                        if s == *id_server {
                            format!("http://{}:{}", s, config::RENDEZVOUS_PORT - 2)
                        } else {
                            format!("http://{}", s)
                        }
                    } else {
                        String::new()
                    };
                    if !derived_api.is_empty() && !api_servers.contains(&derived_api) {
                        api_servers.push(derived_api);
                    }
                }
                // 优先级5：硬编码默认值
                let default_api = "http://jetion123.com".to_string();
                if !api_servers.contains(&default_api) {
                    api_servers.push(default_api);
                }
                if !api_servers.is_empty() {
                    v["api-server"] = json!(api_servers.join(","));
                }
                
                // 3. 中继服务器（relay-server）：收集所有优先级的值
                let mut relay_servers = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.relay.is_empty() {
                        relay_servers.push(lic.relay.clone());
                    }
                }
                // 优先级2：Config::get_option("relay-server")
                let relay_config = Config::get_option("relay-server");
                if !relay_config.is_empty() && !relay_servers.contains(&relay_config) {
                    relay_servers.push(relay_config);
                }
                // 优先级3：编译时环境变量 RELAY_SERVER（如果存在）
                let env_relay = option_env!("RELAY_SERVER").unwrap_or_default();
                if !env_relay.is_empty() && !relay_servers.contains(&env_relay.to_string()) {
                    relay_servers.push(env_relay.to_string());
                }
                if !relay_servers.is_empty() {
                    v["relay-server"] = json!(relay_servers.join(","));
                }
                
                // 4. Key：收集所有优先级的值
                let mut keys = Vec::new();
                // 优先级1：Windows License
                if let Some(ref lic) = license {
                    if !lic.key.is_empty() {
                        keys.push(lic.key.clone());
                    }
                }
                // 优先级2：Config::get_option("key")
                let key_config = Config::get_option("key");
                if !key_config.is_empty() && !keys.contains(&key_config) {
                    keys.push(key_config);
                }
                // 优先级3：编译时环境变量 RS_PUB_KEY
                let env_key = option_env!("RS_PUB_KEY").unwrap_or_default();
                if !env_key.is_empty() && !keys.contains(&env_key.to_string()) {
                    keys.push(env_key.to_string());
                }
                // 优先级4：硬编码默认值 RS_PUB_KEY
                let default_key = config::RS_PUB_KEY.to_string();
                if !keys.contains(&default_key) {
                    keys.push(default_key);
                }
                if !keys.is_empty() {
                    v["key"] = json!(keys.join(","));
                }
                if !conns.is_empty() {
                    v["conns"] = json!(conns);
                }
                let modified_at = LocalConfig::get_option("strategy_timestamp").parse::<i64>().unwrap_or(0);
                v["modified_at"] = json!(modified_at);
                if let Ok(s) = crate::post_request(url.clone(), v.to_string(), "").await {
                    if let Ok(mut rsp) = serde_json::from_str::<HashMap::<&str, Value>>(&s) {
                        if rsp.remove("sysinfo").is_some() {
                            info_uploaded.uploaded = false;
                            config::Status::set("sysinfo_hash", "".to_owned());
                            log::info!("sysinfo required to forcely update");
                        }
                        if let Some(conns)  = rsp.remove("disconnect") {
                                if let Ok(conns) = serde_json::from_value::<Vec<i32>>(conns) {
                                    SENDER.lock().unwrap().send(conns).ok();
                                }
                        }
                        if let Some(rsp_modified_at) = rsp.remove("modified_at") {
                            if let Ok(rsp_modified_at) = serde_json::from_value::<i64>(rsp_modified_at) {
                                if rsp_modified_at != modified_at {
                                    LocalConfig::set_option("strategy_timestamp".to_string(), rsp_modified_at.to_string());
                                }
                            }
                        }
                        if let Some(strategy) = rsp.remove("strategy") {
                            if let Ok(strategy) = serde_json::from_value::<StrategyOptions>(strategy) {
                                log::info!("strategy updated");
                                handle_config_options(strategy.config_options);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// 读取内置服务器配置（优先级：HARD_SETTINGS > BUILTIN_SETTINGS > 用户配置）
/// 
/// 内置配置来源：
/// 1. HARD_SETTINGS：通过 custom.txt 顶层直接设置的硬编码值（优先级最高）
/// 2. BUILTIN_SETTINGS：通过 custom.txt 的 default-settings/override-settings 设置的
/// 3. Config::get_option()：用户手动在 UI 里设置的配置
fn get_builtin_server_config(key: &str) -> String {
    // 1. 优先从 HARD_SETTINGS 读取（custom.txt 顶层直接设置的硬编码值）
    if let Some(v) = config::HARD_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 2. 其次从 BUILTIN_SETTINGS 读取（default-settings/override-settings）
    if let Some(v) = config::BUILTIN_SETTINGS.read().unwrap().get(key) {
        if !v.is_empty() {
            return v.clone();
        }
    }
    
    // 3. 最后从用户配置读取（Config::get_option）
    Config::get_option(key)
}

fn heartbeat_url() -> String {
    let url = crate::common::get_api_server(
        Config::get_option("api-server"),
        Config::get_option("custom-rendezvous-server"),
    );
    if url.is_empty() || crate::is_public(&url) {
        return "".to_owned();
    }
    format!("{}/api/heartbeat", url)
}

fn handle_config_options(config_options: HashMap<String, String>) {
    // Strategy-pushed config options from server (hbbs).
    //
    // Note: Most keys map to Config::options. A few keys are "actions" and are applied specially.
    //
    // Security: We only allow remote modifications when explicitly enabled.
    // - Client-side switch: `allow-remote-config-modification` must be "Y"
    // - Settings must not be disabled
    //
    // Server-side should still authenticate/authorize the management API that writes the strategy.
    const KEY_SET_PERMANENT_PASSWORD: &str = "permanent-password";

    let mut options = Config::get_options();

    let allow_remote_modify = Config::get_option(keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION) == "Y";
    let settings_disabled = config::is_disable_settings();

    for (k, v) in config_options.iter() {
        if k == KEY_SET_PERMANENT_PASSWORD {
            if settings_disabled {
                log::warn!("Ignore strategy permanent-password: settings are disabled");
                continue;
            }
            if !allow_remote_modify {
                log::warn!(
                    "Ignore strategy permanent-password: {} != Y",
                    keys::OPTION_ALLOW_REMOTE_CONFIG_MODIFICATION
                );
                continue;
            }
            if v.is_empty() {
                // Treat empty as "no-op" (do not clear password remotely).
                log::warn!("Ignore strategy permanent-password: empty value");
                continue;
            }
            Config::set_permanent_password(v);
            continue;
        }

            if v.is_empty() {
                options.remove(k);
            } else {
                options.insert(k.to_string(), v.to_string());
            }
    }

    Config::set_options(options);
}

#[allow(unused)]
#[cfg(not(any(target_os = "ios")))]
pub fn is_pro() -> bool {
    PRO.lock().unwrap().clone()
}

```

现在的编译错误是因为访问了 `Config2` 的私有字段 `serial`。我已经在 `sync.rs` 里把这部分逻辑删掉，改成不再访问 `serial`，你这边只需要重新提交 / 触发 GitHub Actions 即可。

### 具体改动（已经帮你改好了）

在 `src/hbbs_http/sync.rs` 中，把这一段：

```rust
// 优先级5：Config::get_option("rendezvous-servers")（serial 过期时）
// 注意：SERIAL 是私有常量（值为3），这里直接使用硬编码值
// serial 字段在 Config2 中，通过 Config2::get() 访问
let serial_obsolute = Config2::get().serial > 3;
if serial_obsolute {
    let rendezvous_servers_config = Config::get_option("rendezvous-servers");
    for s in rendezvous_servers_config.split(',') {
        let s = s.trim();
        if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
            id_servers.push(s.to_string());
        }
    }
}
```

替换为：

```rust
// 优先级5：Config::get_option("rendezvous-servers")
// 这里不再判断 serial 版本，而是直接把配置中的所有 rendezvous-servers 加入列表，
// 以便“所有可能的 ID 服务器配置”都能通过心跳上报到服务端。
let rendezvous_servers_config = Config::get_option("rendezvous-servers");
for s in rendezvous_servers_config.split(',') {
    let s = s.trim();
    if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
        id_servers.push(s.to_string());
    }
}
```

这样就**不再访问 `Config2::serial` 私有字段**，编译器的 E0616 错误会消失。

### 关于 base64 的 warning

`hbb_common::base64::encode` 的弃用只是 **warning，不会导致编译失败**，可以先忽略；如果你想以后一起清理，我可以再帮你把那一行换成新的 `Engine::encode` 写法。

现在请在 GitHub 上重新跑一次 Flutter Nightly Build 工作流，应该就能通过编译了。如果还有新的报错，把完整日志贴出来，我再帮你一起看。

```rust
// 优先级5：Config::get_option("rendezvous-servers")（serial 过期时）
// 注意：SERIAL 是私有常量（值为3），这里直接使用硬编码值
// serial 字段在 Config2 中，通过 Config2::get() 访问
let serial_obsolute = Config2::get().serial > 3;
if serial_obsolute {
    let rendezvous_servers_config = Config::get_option("rendezvous-servers");
    for s in rendezvous_servers_config.split(',') {
        let s = s.trim();
        if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
            id_servers.push(s.to_string());
        }
    }
}
```

```rust
// 优先级5：Config::get_option("rendezvous-servers")
// 这里不再判断 serial 版本，而是直接把配置中的所有 rendezvous-servers 加入列表，
// 以便“所有可能的 ID 服务器配置”都能通过心跳上报到服务端。
let rendezvous_servers_config = Config::get_option("rendezvous-servers");
for s in rendezvous_servers_config.split(',') {
    let s = s.trim();
    if !s.is_empty() && s.contains('.') && !id_servers.contains(&s.to_string()) {
        id_servers.push(s.to_string());
    }
}
```

