import React from 'react';
import { graphql } from "gatsby"
import Layout, { BREAK_POINT } from '../components/Layout';
import 'github-markdown-css/github-markdown.css';
import styled from 'styled-components';

const MarkdownContainer = styled.div`
  font-size: var(--dara-font-14);
  color: var(--dara-text);
  background: transparent;

  h1 { font-size: var(--dara-font-20); }
  h2 { font-size: var(--dara-font-16); }
  h3 { font-size: var(--dara-font-14); }
  h1, h2 { border-bottom-color: var(--dara-border); }

  p {
    line-height: 160%;
  }
  
  .center {
    text-align: center;
  }

  @media (max-width: ${BREAK_POINT}px) {
    h1, h2 {
      border-bottom: none;
      padding-bottom: 0.1em;
      margin-bottom: 8px;
    }
`;

export default function Template({ data }) {
  const { markdownRemark } = data;
  const { frontmatter, html } = markdownRemark;
  return (
    <Layout hasSider={frontmatter.hasSider} title={frontmatter.title}>
      <MarkdownContainer
        style={{
          maxWidth: frontmatter.hasSider ? "750px" : "75ch"
        }}
        className="markdown-body markdown-container"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </Layout>
  )
}

export const pageQuery = graphql`
  query ($slug: String) {
    markdownRemark(frontmatter: { slug: { eq: $slug } }) {
      html
      frontmatter {
        slug
        hasSider
        title
      }
    }
  }
`