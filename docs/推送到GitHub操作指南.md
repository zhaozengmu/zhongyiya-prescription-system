# 把系统推到 GitHub：完整操作指南

> 适用仓库：`zhaozengmu/zhongyiya-prescription-system`
> 本地目录：`C:\Users\Lenovo\zhongyiya-prescription-system`

---

## 一、现在的状态：已经推上去了 ✅

**2026-11-03 23:07 完成推送**，远程从 `442b5ca` 更新到 `efeaa22`，本地与远程完全同步。

| 项目 | 状态 |
|---|---|
| 推送结果 | ✅ 成功，10 个提交已上传 |
| 本地分支 | `main`，与 `origin/main` 同步（0 个待推送）|
| 远程最新提交 | `efeaa22` 公网脚本仅在真实控制台切 GBK 编码… |
| 网络 | 已实测直连 GitHub 正常（约 1 秒），**不需要配代理** |
| 仓库地址 | <https://github.com/zhaozengmu/zhongyiya-prescription-system> |

下面这些内容留着给**下次改完代码**时用。

---

## 二、先造这把钥匙：Personal Access Token

GitHub 从 2021 年起就不再接受账号密码了，推送必须用 Token。

1. 浏览器打开 GitHub，登录 `zhaozengmu` 账号
2. 右上角头像 → **Settings**
3. 左侧最下方 → **Developer settings**
4. 展开 **Personal access tokens** → 点 **Tokens (classic)**
5. 右上角 **Generate new token** → **Generate new token (classic)**
   - 如果弹出输入密码，正常输入
6. 填写：
   - **Note**：`workbuddy-push`（随便写，方便以后认出来）
   - **Expiration**：30 days（够用了，用完可以撤销）
   - **Select scopes**：**只勾 `repo` 这一整个大类**（它下面 5 个小项会自动全选）
7. 拉到底 → **Generate token**
8. 页面上会出现一串 `ghp_xxxxxxxx...`，**立刻复制**（离开页面就再也看不到了）

> 小提示：classic token 的正确长度是 **40 个字符**（`ghp_` 开头）。如果复制出来明显短很多，说明没复制全，重新复制一次。

---

## 三、三条推送路线，选一条就行

### 路线 A：双击桌面的「推送到GitHub.bat」（最省事）

桌面上已经放好了 `推送到GitHub.bat`：

1. 双击它
2. 它会显示"本地领先远程 9 个提交"
3. 提示时**粘贴刚才的 Token，回车**
4. 看到「推送成功」就好了

这个脚本做的事：用 Token 直接推 `main` 分支 → 推完自动把 Token 从仓库地址里清掉（不会留在配置里）。

---

### 路线 B：自己在 PowerShell 里推

1. 按 `Win + X` → 选 **终端** / **Windows PowerShell**
2. 依次输入：

```powershell
cd C:\Users\Lenovo\zhongyiya-prescription-system
git push origin main
```

3. 弹出登录窗口时：
   - **用户名**：`zhaozengmu`
   - **密码**：粘贴 Token（不是 GitHub 登录密码！）

如果卡住不弹窗，先执行一次下面这句，再重新 push：

```powershell
git config --global --unset credential.helper
git config --global credential.helper manager
```

> 注：你机器上全局的 credential.helper 现在指向一个内部工具目录里的管理器，
> 有时候会卡住不响应。上面的命令把它换成标准配置。

---

### 路线 C：把 Token 发我，我替你推

把 Token 贴给我，我执行：

```bash
git -c credential.helper= push \
  "https://zhaozengmu:<TOKEN>@github.com/zhaozengmu/zhongyiya-prescription-system.git" main
git remote set-url origin https://github.com/zhaozengmu/zhongyiya-prescription-system.git
```

推完我会立刻把 Token 从远程地址里去掉。**推完记得去 GitHub 把这枚 Token 撤销**
（Settings → Developer settings → Tokens → 点进去 → Delete）。

---

## 四、以后每次改完代码，怎么推（三步）

```powershell
cd C:\Users\Lenovo\zhongyiya-prescription-system
git add -A
git commit -m "这次改了什么，一句话说清"
git push
```

第一次验证通过后，Token 会被系统记住，后面 `git push` 不会再问。

---

## 五、哪些东西**不会**进仓库（这是对的，别手动塞）

`.gitignore` 已经挡住了这些：

| 文件 | 为什么不传 |
|---|---|
| `.env` | 里面是 SECRET_KEY、数据库加密密钥，传上去等于把钥匙公开 |
| `db.sqlite3` | 本地数据库，体积大且含真实数据 |
| `.venv/` | 虚拟环境，几十万个文件，每台机器自己生成 |

别人 clone 下来后，`.env` 会自动生成、数据库会自动建（见下一节），所以不影响使用。

---

## 六、别人拿到仓库后怎么跑起来

```powershell
git clone https://github.com/zhaozengmu/zhongyiya-prescription-system.git
```

然后**双击项目里的 `一键启动.bat`**，它会自动：

1. 找到可用的 Python（3.10+）
2. 建虚拟环境 `.venv`
3. 装 4 个依赖（Django 5.2.18 + cryptography + python-dotenv + openpyxl）
4. 生成 `.env`（随机 SECRET_KEY 和加密密钥）
5. 建库 + 灌演示数据（8 个角色、27 项权限、43 味药材、示例挂号/病历）
6. 打开浏览器 http://127.0.0.1:8000

命令行等价做法：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -i https://mirrors.aliyun.com/pypi/simple Django==5.2.18 cryptography python-dotenv openpyxl
.venv\Scripts\python init_env.py
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py init_data
.venv\Scripts\python manage.py runserver
```

---

## 七、演示账号

| 账号 | 角色 |
|---|---|
| `admin1` | 系统管理员 |
| `doctor1` ~ `doctor5` | 医师 |
| `pharmacist1` | 药师 |
| `pharmacist2` | 药师组长 |
| `cashier1` | 收费员 |
| `pharmacy1` | 药房管理员 |
| `patient1` / `patient2` | 患者 |

**统一密码：`rx123456`**（登录页已不显示这些账号，需要手动输入）

---

## 八、报错对照表

| 报错 | 原因 | 怎么办 |
|---|---|---|
| `could not read Username for 'https://github.com'` | 没有凭据 | 用路线 A/B/C 任意一种 |
| `Authentication failed` | Token 复制不全 / 已过期 | 重新生成一个 |
| `remote: Permission to ... denied` | Token 没有 `repo` 权限 | 重新生成，勾上 `repo` |
| `refusing to allow a Personal Access Token to create or update workflow` | Token 缺 `workflow` 权限，且推的内容里有 `.github/workflows/` | 本项目没有该目录，正常不会遇到；真遇到就给 Token 补勾 `workflow` |
| `Failed to connect to github.com port 443` | 网络抖动 | 等一会重试；本项目已实测可直连 |
| `src refspec main does not match any` | 分支名不是 main | `git branch -M main` 后重试 |

---

## 九、推送成功后确认

浏览器打开 <https://github.com/zhaozengmu/zhongyiya-prescription-system>

- 最新提交应该是「公网脚本仅在真实控制台切 GBK 编码…」
- 提交数比之前多 10 个
- 仓库里**看不到** `.env`、`db.sqlite3`、`.venv`（这是正确的，已核对）
