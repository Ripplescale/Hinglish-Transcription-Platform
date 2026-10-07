; Rename or remove only old shortcuts that point to this installation. Recordings,
; preferences, models and the STTApp data directory are never touched.
!macro NSIS_HOOK_POSTINSTALL
  ${If} $NoShortcutMode <> 1
    !insertmacro IsShortcutTarget "$SMPROGRAMS\xx.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
    Pop $0
    ${If} $0 = 1
      ; /UPDATE may retain the previous Start menu shortcut instead of
      ; creating one under the new display name. Preserve that entry.
      ${IfNot} ${FileExists} "$SMPROGRAMS\${PRODUCTNAME}.lnk"
        !insertmacro UnpinShortcut "$SMPROGRAMS\xx.lnk"
        Rename "$SMPROGRAMS\xx.lnk" "$SMPROGRAMS\${PRODUCTNAME}.lnk"
      ${Else}
        !insertmacro IsShortcutTarget "$SMPROGRAMS\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
        Pop $0
        ${If} $0 = 1
          !insertmacro UnpinShortcut "$SMPROGRAMS\xx.lnk"
          Delete "$SMPROGRAMS\xx.lnk"
        ${EndIf}
      ${EndIf}
    ${EndIf}
    !insertmacro IsShortcutTarget "$DESKTOP\xx.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
    Pop $0
    ${If} $0 = 1
      ; Preserve an existing desktop shortcut even when the installer finish
      ; page is not asked to create a new one. Never replace another shortcut.
      ${IfNot} ${FileExists} "$DESKTOP\${PRODUCTNAME}.lnk"
        !insertmacro UnpinShortcut "$DESKTOP\xx.lnk"
        Rename "$DESKTOP\xx.lnk" "$DESKTOP\${PRODUCTNAME}.lnk"
      ${Else}
        !insertmacro IsShortcutTarget "$DESKTOP\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
        Pop $0
        ${If} $0 = 1
          !insertmacro UnpinShortcut "$DESKTOP\xx.lnk"
          Delete "$DESKTOP\xx.lnk"
        ${EndIf}
      ${EndIf}
    ${EndIf}
  ${EndIf}
!macroend
