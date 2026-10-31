// Microsoft Foundry (formerly Azure AI Foundry) environment for the incident agents:
// Foundry resource + project, a chat model deployment, Log Analytics and Application
// Insights for traces, and keyless (Entra ID) access for the developer / workload identity.
targetScope = 'resourceGroup'

@description('Prefix for resource names (lowercase, 3-12 chars)')
@minLength(3)
@maxLength(12)
param prefix string = 'incagents'

param location string = resourceGroup().location

@description('Chat model to deploy')
param modelName string = 'gpt-4.1-mini'
param modelVersion string = '2025-04-14'

@description('Deployment capacity in thousands of tokens per minute')
param modelCapacity int = 50

@description('Object id of the user, group or managed identity that runs the agents')
param principalId string = ''

@allowed(['User', 'Group', 'ServicePrincipal'])
param principalType string = 'User'

var suffix = uniqueString(resourceGroup().id)
var foundryName = toLower('${prefix}-${suffix}')
var foundryUserRoleId = '53ca6127-db72-4b80-b1b0-d745d6d5456d' // Foundry User (formerly Azure AI User)

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs-${suffix}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-appi-${suffix}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryName
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: foundryName // required for Entra ID token auth
    disableLocalAuth: true // keys disabled: Entra ID only
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: '${prefix}-project'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Incident triage agents'
    description: 'Multi-agent incident triage for industrial IoT'
  }
}

resource chatModel 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: modelName
  sku: {
    name: 'GlobalStandard'
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
  }
}

resource foundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(foundry.id, principalId, foundryUserRoleId)
  scope: foundry
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUserRoleId)
  }
}

output AZURE_OPENAI_ENDPOINT string = foundry.properties.endpoint
output AZURE_OPENAI_DEPLOYMENT string = chatModel.name
output FOUNDRY_PROJECT_NAME string = project.name
output APPLICATIONINSIGHTS_CONNECTION_STRING string = appInsights.properties.ConnectionString
