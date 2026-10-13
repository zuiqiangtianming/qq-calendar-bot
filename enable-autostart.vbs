Option Explicit

' 一键设置 / 取消「开机自启」：把 autostart.vbs 复制进「启动」文件夹。
'   双击运行                -> 安装
'   wscript 本文件 remove   -> 卸载
' 说明：复制而不是建快捷方式，免去 COM 依赖，也更不容易被杀软拦。

Const DEFAULT_ROOT = "C:\Users\jzd\Documents\qq-calendar-bot"
Const TARGET_NAME  = "QQ Calendar Bot autostart.vbs"

Dim fso, sh, root, startup, target, mode
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(WScript.ScriptFullName)
If Not fso.FileExists(root & "\autostart.vbs") Then root = DEFAULT_ROOT

mode = "install"
If WScript.Arguments.Count > 0 Then mode = LCase(WScript.Arguments(0))

startup = sh.SpecialFolders("Startup")
target  = startup & "\" & TARGET_NAME

If mode = "remove" Then
    If fso.FileExists(target) Then
        fso.DeleteFile target, True
        MsgBox "已取消开机自启。", 64, "QQ 日程管家"
    Else
        MsgBox "本来就没有设置开机自启。", 64, "QQ 日程管家"
    End If
    WScript.Quit 0
End If

If Not fso.FileExists(root & "\autostart.vbs") Then
    MsgBox "找不到 autostart.vbs，请确认项目文件还在原位置。", 16, "QQ 日程管家"
    WScript.Quit 1
End If

fso.CopyFile root & "\autostart.vbs", target, True
MsgBox "已设置开机自启。" & vbCrLf & vbCrLf & _
       "文件：" & target & vbCrLf & vbCrLf & _
       "取消：再次运行本文件并加 remove 参数，或直接删掉该文件。", 64, "QQ 日程管家"
