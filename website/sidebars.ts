import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docsSidebar: [
    {
      type: 'doc',
      id: 'intro',
      label: 'Introduction',
    },
    {
      type: 'category',
      label: 'Getting Started',
      items: [
        'getting-started/installation',
        'getting-started/quick-start',
        'getting-started/concepts',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      items: [
        'guides/library-api',
        'guides/cli',
        'guides/querying',
        'guides/visualization',
        'guides/neo4j',
        'guides/cross-language',
        'guides/mcp-server',
      ],
    },
    {
      type: 'category',
      label: 'CI Integration',
      items: [
        'ci-integration/overview',
        'ci-integration/github-actions',
        'ci-integration/docker',
      ],
    },
    {
      type: 'category',
      label: 'Adapters',
      items: [
        'adapters/overview',
        'adapters/python',
        'adapters/typescript',
        'adapters/go',
        'adapters/rust',
        'adapters/writing-an-adapter',
      ],
    },
    {
      type: 'category',
      label: 'Graph Model',
      items: [
        'graph-model/nodes',
        'graph-model/relations',
        'graph-model/boundaries',
        'graph-model/serialization',
      ],
    },
    {
      type: 'category',
      label: 'API Reference',
      items: [
        'api-reference/graphlens',
        'api-reference/models',
        'api-reference/registry',
        'api-reference/contracts',
        'api-reference/cli',
        'api-reference/exceptions',
      ],
    },
  ],
};

export default sidebars;
