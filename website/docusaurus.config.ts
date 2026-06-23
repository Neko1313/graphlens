import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'graphlens',
  tagline: 'Extensible polyglot code analysis framework with a shared graph IR',
  favicon: 'img/logo.svg',

  future: {
    v4: true,
  },

  url: 'https://Neko1313.github.io',
  baseUrl: '/graphlens/',

  organizationName: 'Neko1313',
  projectName: 'graphlens',

  onBrokenLinks: 'throw',

  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/Neko1313/graphlens/tree/main/website/',
          routeBasePath: 'docs',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'graphlens',
      logo: {
        alt: 'graphlens logo',
        src: 'img/logo.svg',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docsSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          to: '/docs/api-reference/graphlens',
          label: 'API',
          position: 'left',
        },
        {
          href: 'https://github.com/Neko1313/graphlens',
          label: 'GitHub',
          position: 'right',
        },
        {
          href: 'https://pypi.org/project/graphlens/',
          label: 'PyPI',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {label: 'Introduction', to: '/docs/'},
            {label: 'Getting Started', to: '/docs/getting-started/installation'},
            {label: 'Guides', to: '/docs/guides/library-api'},
            {label: 'API Reference', to: '/docs/api-reference/graphlens'},
          ],
        },
        {
          title: 'Topics',
          items: [
            {label: 'CI Integration', to: '/docs/ci-integration/overview'},
            {label: 'Adapters', to: '/docs/adapters/overview'},
            {label: 'Graph Model', to: '/docs/graph-model/nodes'},
            {label: 'Cross-language', to: '/docs/guides/cross-language'},
          ],
        },
        {
          title: 'Links',
          items: [
            {
              label: 'GitHub',
              href: 'https://github.com/Neko1313/graphlens',
            },
            {
              label: 'PyPI',
              href: 'https://pypi.org/project/graphlens/',
            },
            {
              label: 'Issues',
              href: 'https://github.com/Neko1313/graphlens/issues',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} graphlens. Built with Docusaurus.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['python', 'php', 'bash', 'toml', 'json', 'docker', 'cypher'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
