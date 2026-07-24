{{- define "recruitment-assistant.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "recruitment-assistant.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- include "recruitment-assistant.name" . -}}
{{- end -}}
{{- end -}}

{{- define "recruitment-assistant.labels" -}}
app.kubernetes.io/name: {{ include "recruitment-assistant.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "recruitment-assistant.selectorLabels" -}}
app.kubernetes.io/name: {{ include "recruitment-assistant.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "recruitment-assistant.pvcName" -}}
{{- if .Values.persistence.existingClaim -}}
{{- .Values.persistence.existingClaim -}}
{{- else -}}
{{- printf "%s-runtime" (include "recruitment-assistant.fullname" .) -}}
{{- end -}}
{{- end -}}
