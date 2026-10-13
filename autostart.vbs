Option Explicit

' QQ 日程管家 —— 开机自启用的静默启动器
' 用 pythonw.exe（无控制台）拉起 bot.py，不弹黑窗口；输出写进 logs\bot.log。
' 不要直接双击它做日常启动（那样没窗口、看不到日志），日常请用 start.bat。

' 开关（控制开机要不要自动起来）：任务管理器 -> 启动 页，找到
'   QQ Calendar Bot autostart，右键 启用 / 禁用。禁用不删文件，随时可恢复。
' 只想现在停掉这个进程：taskkill /F /IM pythonw.exe

Const DEFAULT_ROOT = "C:\Users\jzd\Documents\qq-calendar-bot"

Dim fso, sh, root, pyw, script
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

' 优先用本文件所在目录；如果那儿没有 bot.py（比如被复制到别处），退回默认路径
root = fso.GetParentFolderName(WScript.ScriptFullName)
If Not fso.FileExists(root & "\bot.py") Then root = DEFAULT_ROOT

pyw    = root & "\.venv\Scripts\pythonw.exe"
script = root & "\bot.py"

If Not fso.FileExists(pyw) Then
    MsgBox "找不到：" & pyw & vbCrLf & vbCrLf & _
           "请先在该目录里建好虚拟环境（详见 README）。", 16, "QQ 日程管家"
    WScript.Quit 1
End If

If Not fso.FileExists(root & "\.env") Then
    MsgBox "找不到 .env，请先把 .env.example 复制成 .env 并填入 AppID / AppSecret。", 16, "QQ 日程管家"
    WScript.Quit 1
End If

sh.CurrentDirectory = root
' 参数：命令、窗口样式（0=隐藏）、是否等它结束（False=不等待）
sh.Run """" & pyw & """ """ & script & """", 0, False
